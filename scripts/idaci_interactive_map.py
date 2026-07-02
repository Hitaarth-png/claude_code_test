#!/usr/bin/env python3
"""
Build a self-contained, interactive Leaflet map of IDACI (Income Deprivation
Affecting Children Index) deprivation across Leicester LSOAs.

Input: the 2025 Indices of Deprivation extract for Leicester (semicolon
delimited CSV, one row per LSOA, with an embedded GeoJSON polygon per row in
the "LSOAGeo Shape" column).

Output: a single HTML file with the map, legend, ward filter and per-area
popups. No server or external data files are needed to view it - just open
it in a browser (an internet connection is needed once, to load the Leaflet
library and basemap tiles from their CDNs).
"""
import argparse
import csv
import json
from pathlib import Path

# IDACI decile 1 = most deprived 10% nationally, 10 = least deprived.
# Colour ramp goes dark red (most deprived) -> pale yellow (least deprived).
DECILE_COLOURS = {
    1: "#67000d",
    2: "#a50f15",
    3: "#cb181d",
    4: "#ef3b2c",
    5: "#fb6a4a",
    6: "#fc9272",
    7: "#fcbba1",
    8: "#fee0d2",
    9: "#fff5eb",
    10: "#ffffe5",
}
NO_DATA_COLOUR = "#cccccc"

# Total number of LSOAs in England (2021 Census geography) that IDACI ranks
# are drawn from - used to convert a rank into a national percentile.
# Derived from this dataset's own decile boundaries (rank cutoffs are only
# consistent with a total of 33,755), cross-checked against the published
# 2021 LSOA count for England.
ENGLAND_LSOA_COUNT = 33755

# Year 6 obesity (incl. severe obesity) prevalence by MSOA, 2021/22-2023/24
# 3-year average. Source: Leicester City Council NCMP 2023/24 report - these
# are the only MSOAs given an exact percentage in the report text (others
# are only shown as a colour band on a map image, which we're not using
# since it's ambiguous which age group's scale it reflects). Leicester city
# average for context: 25.6%.
YEAR6_OBESITY_PCT_BY_MSOA = {
    "Kirby Frith": 31.9,
    "Newfoundpool": 31.9,
    "Bradgate Heights & Beaumont Leys": 30.6,
    "Stocking Farm & Mowmacre": 29.6,
    "Clarendon Park & Stoneygate South": 18.4,
    "Knighton": 16.3,
}

# Colours per GIAS "EstablishmentTypeGroup", used for the schools overlay.
SCHOOL_CATEGORY_COLOURS = {
    "Academies": "#1f78b4",
    "Local authority maintained schools": "#33a02c",
    "Children's Centres": "#ff7f00",
    "Independent schools": "#6a3d9a",
    "Special schools": "#e31a1c",
}
SCHOOL_DEFAULT_COLOUR = "#666666"

# Categories excluded from the schools overlay entirely - not the day-to-day
# youth-facing provision this map is focused on.
EXCLUDED_SCHOOL_CATEGORIES = {"Free Schools", "Colleges", "Universities", "Other types"}


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def load_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = [h.lstrip("﻿") for h in next(reader)]
        for raw_row in reader:
            if not raw_row or not raw_row[0]:
                continue
            yield dict(zip(header, raw_row))


def build_features(rows):
    features = []
    for row in rows:
        try:
            geometry = json.loads(row["LSOAGeo Shape"])
        except (KeyError, ValueError):
            continue

        pop_0015 = to_int(row.get("Pop_0015_2022"))
        pop_total = to_int(row.get("Pop_2022"))
        msoa_name = row.get("MSOA HCL Name") or row.get("MSOA Name")

        props = {
            "lsoa_code": row.get("LSOA code"),
            "lsoa_name": row.get("LSOA name"),
            "ward": row.get("Ward Name"),
            "msoa_name": msoa_name,
            "year6_obesity_pct": YEAR6_OBESITY_PCT_BY_MSOA.get(msoa_name),
            "parliamentary_constituency": row.get("Parliamentary Constituency"),
            "idaci_score": (
                round(to_float(row.get("IDACI_score")) * 100, 1)
                if to_float(row.get("IDACI_score")) is not None
                else None
            ),
            "idaci_rank": to_int(row.get("IDACI_rank")),
            "idaci_percentile": (
                round(to_int(row.get("IDACI_rank")) / ENGLAND_LSOA_COUNT * 100, 1)
                if to_int(row.get("IDACI_rank")) is not None
                else None
            ),
            "idaci_decile": to_int(row.get("IDACI_decile")),
            "idaci_quintile": to_int(row.get("IDACI_quintile")),
            "imd_score": to_float(row.get("IMD2025_score")),
            "imd_decile": to_int(row.get("IMD2025_decile")),
            "pop_0015": pop_0015,
            "pop_total": pop_total,
            "pct_children_0015": round(100 * pop_0015 / pop_total, 1)
            if pop_0015 is not None and pop_total
            else None,
        }
        features.append({"type": "Feature", "geometry": geometry, "properties": props})
    return features


def build_ward_boundaries(features):
    """Dissolve LSOA polygons into one outline per administrative ward, for
    a distinct ward-boundary overlay (LSOAs are the smallest unit the source
    data carries geometry for; wards are drawn by merging their LSOAs)."""
    from collections import defaultdict

    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    by_ward = defaultdict(list)
    for f in features:
        ward = f["properties"].get("ward")
        if not ward:
            continue
        try:
            by_ward[ward].append(shape(f["geometry"]))
        except (ValueError, TypeError):
            continue

    ward_features = []
    for ward, geoms in by_ward.items():
        merged = unary_union(geoms).buffer(0)
        ward_features.append(
            {
                "type": "Feature",
                "geometry": mapping(merged),
                "properties": {"ward": ward, "lsoa_count": len(geoms)},
            }
        )
    return ward_features


def load_school_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_school_features(rows):
    """Convert a GIAS (Get Information about Schools) establishment export
    into point features. Easting/Northing (British National Grid, EPSG:27700)
    are converted to WGS84 lon/lat for Leaflet. Only public, facility-level
    fields are kept (name, type, address, aggregate pupil counts) - no head
    teacher name or contact details."""
    from pyproj import Transformer

    to_wgs84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)

    features = []
    skipped = 0
    for row in rows:
        status = (row.get("EstablishmentStatus (name)") or "").strip()
        if not status.startswith("Open"):
            continue

        category = row.get("EstablishmentTypeGroup (name)") or "Other types"
        if category in EXCLUDED_SCHOOL_CATEGORIES:
            continue

        easting = to_float(row.get("Easting"))
        northing = to_float(row.get("Northing"))
        if easting is None or northing is None:
            skipped += 1
            continue
        lon, lat = to_wgs84.transform(easting, northing)

        address_parts = [
            row.get("Street"),
            row.get("Locality"),
            row.get("Town"),
            row.get("Postcode"),
        ]
        address = ", ".join(p for p in address_parts if p)

        pupils = to_int(row.get("NumberOfPupils"))
        pct_fsm = to_float(row.get("PercentageFSM"))

        props = {
            "urn": row.get("URN"),
            "name": row.get("EstablishmentName"),
            "category": category,
            "type": row.get("TypeOfEstablishment (name)"),
            "phase": row.get("PhaseOfEducation (name)"),
            "status": status,
            "age_low": to_int(row.get("StatutoryLowAge")),
            "age_high": to_int(row.get("StatutoryHighAge")),
            "address": address or None,
            "postcode": row.get("Postcode") or None,
            "ward": row.get("AdministrativeWard (name)") or None,
            "pupils": pupils,
            "pct_fsm": pct_fsm,
            "website": row.get("SchoolWebsite") or None,
        }
        features.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}
        )

    if skipped:
        print(f"Warning: skipped {skipped} school rows with no Easting/Northing")
    return features


# Leicester "playing pitches by site" audit has no coordinates - only site
# names. Where a site name matches a GIAS school (allowing for the source
# CSV's typos/renames), we plot it at that school's location. `approx=True`
# marks matches that are a school's grounds/stadium rather than the school
# itself, so the popup can flag it as approximate.
PITCH_SITE_TO_SCHOOL = {
    "Babington Community College": ("Babington Academy", False),
    "Beamont Leys School": ("Beaumont Leys School", False),
    "Beamont Lodge Primary School": ("Beaumont Lodge Primary School", False),
    "Crown Hills School": ("Crown Hills Community College", False),
    "English Martyrs School": ("English Martyrs' Catholic School, A Voluntary Academy", False),
    "Fulhurst Community College": ("Fullhurst Community College", False),
    "Gateway College": ("Gateway Sixth Form College", False),
    "Heatherbrook Primary School": ("Heatherbrook Primary Academy", False),
    "Judgemeadow School": ("Judgemeadow Community College", False),
    "Soar Valley College": ("Soar Valley College", False),
    "St Pauls Catholic School": ("St Paul's Catholic School, a Voluntary Academy", False),
    "The Lancaster School": ("Lancaster Academy", False),
    "Wyggeston and QE College": ("WQE and Regent College Group", False),
    "Rushey Mead School Stadium": ("Rushey Mead Academy", True),
    "Willowbrook Primary School": ("Willowbrook Mead Primary Academy", True),
}


def load_pitch_inventory_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_pitch_features_from_inventory(rows, school_features):
    """Aggregate the Leicester playing-pitches-by-site CSV (Sub Area, Site
    Name, Access, Number of Pitches, Pitch Type, Pitch Capacity, Rating, Is
    3G/AGP) up to one marker per site, and place sites that share a name
    with a known school at that school's coordinates (see
    PITCH_SITE_TO_SCHOOL). Sites with no name match are returned separately
    so the caller can report what's still missing a location."""
    school_coords = {f["properties"]["name"]: f["geometry"]["coordinates"] for f in school_features}

    by_site = {}
    for row in rows:
        name = (row.get("Site Name") or "").strip()
        if not name:
            continue
        by_site.setdefault(name, []).append(row)

    features = []
    unmatched = []
    for name, items in by_site.items():
        school_name, approx = PITCH_SITE_TO_SCHOOL.get(name, (None, False))
        coords = school_coords.get(school_name) if school_name else None
        if coords is None:
            unmatched.append(name)
            continue

        pitch_rows = [i for i in items if (i.get("Pitch Type") or "").strip() != "(none currently marked)"]
        total_pitches = sum(to_int(i.get("Number of Pitches")) or 0 for i in pitch_rows)
        total_capacity = sum(to_int(i.get("Pitch Capacity")) or 0 for i in pitch_rows)
        type_counts = {}
        for i in pitch_rows:
            t = (i.get("Pitch Type") or "").strip()
            n = to_int(i.get("Number of Pitches")) or 0
            type_counts[t] = type_counts.get(t, 0) + n
        has_3g = any("3g" in (i.get("Pitch Type") or "").lower() for i in pitch_rows) or any(
            (i.get("Is 3G / AGP") or "").strip().lower() == "yes" for i in pitch_rows
        )

        props = {
            "name": name,
            "sub_area": items[0].get("Sub Area"),
            "access": items[0].get("Access"),
            "total_pitches": total_pitches,
            "total_capacity": total_capacity,
            "pitch_types": type_counts,
            "has_3g": has_3g,
            "matched_school": school_name,
            "approx_location": approx,
        }
        features.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": coords}, "properties": props}
        )

    return features, unmatched


# The youth centres CSV has postcodes but no coordinates. Looked up via
# public UK postcode lookup (postcode-unit centroid, i.e. accurate to a
# handful of neighbouring addresses). LE2 6LE (Kingfisher) couldn't be found
# this way, so it falls back to the Eyres Monsell ward centroid instead -
# `approx=True` flags that one as coarser than the rest.
YOUTH_CENTRE_POSTCODE_COORDS = {
    "LE4 6JD": (52.650907, -1.120608, False),
    "LE5 1HF": (52.645462, -1.061196, False),
    "LE3 6RJ": (52.6445, -1.1808, False),
    "LE1 2PD": (52.639506, -1.120452, False),
    "LE2 6LE": (52.590578, -1.145732, True),
}


def load_youth_centre_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_youth_centre_features(rows):
    features = []
    unmatched = []
    for row in rows:
        name = (row.get("Youth Centre") or "").strip()
        postcode = (row.get("Postcode") or "").strip()
        if not name:
            continue
        coords = YOUTH_CENTRE_POSTCODE_COORDS.get(postcode)
        if coords is None:
            unmatched.append(name)
            continue
        lat, lon, approx = coords

        props = {
            "name": name,
            "address": row.get("Address"),
            "postcode": postcode,
            "ward": row.get("Likely Ward (unverified - see note)"),
            "age_range": row.get("Age Range"),
            "session_days": row.get("Session Days"),
            "focus": row.get("Focus/Notes"),
            "approx_location": approx,
        }
        features.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}
        )
    return features, unmatched


# Social mobility partners CSV has organisation-level notes, not
# coordinates. Only organisations with a specific Leicester address can be
# plotted; others (e.g. national charities that deliver only through
# unnamed local partners) are reported as skipped rather than guessed.
SOCIAL_MOBILITY_PARTNER_COORDS = {
    "Leicestershire Cares": (52.627183, -1.129334, "42 Tower Street, Leicester, LE1 6WT"),
}


def load_social_mobility_partner_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_social_mobility_partner_features(rows):
    features = []
    unmatched = []
    for row in rows:
        name = (row.get("Organisation") or "").strip()
        if not name:
            continue
        coords = SOCIAL_MOBILITY_PARTNER_COORDS.get(name)
        if coords is None:
            unmatched.append(name)
            continue
        lat, lon, address = coords

        props = {
            "name": name,
            "type": row.get("Type"),
            "scope": row.get("Geographic Scope"),
            "focus": row.get("Focus Area"),
            "notes": row.get("Notes"),
            "address": address,
        }
        features.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}
        )
    return features, unmatched


# Football providers CSV gives a base/address in free text, not
# coordinates. Organisations with a specific, single site are matched here
# either to a known landmark address (looked up via public postcode data)
# or to a school already on the map (when their "home ground" is a school
# we've already geocoded). Organisations that operate city-wide with no
# single site (private coaching delivered in many venues, clubs with only a
# vague area name) are reported as skipped rather than guessed.
FOOTBALL_PROVIDER_TO_SCHOOL = {
    "AFC Leicester Ladies and Girls": "Babington Academy",
}
FOOTBALL_PROVIDER_COORDS = {
    "Leicester City in the Community (LCitC)": (52.6206, -1.1428, "King Power Stadium, Filbert Way, Leicester, LE2 7FL"),
    "Leicester Lions RFC (hosts leagues)": (52.56499, -1.169145, "Lutterworth Road, Blaby, Leicester, LE8 4DY"),
}


def load_football_provider_rows(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield row


def build_football_provider_features(rows, school_features):
    school_coords = {f["properties"]["name"]: f["geometry"]["coordinates"] for f in school_features}

    features = []
    unmatched = []
    for row in rows:
        name = (row.get("Organisation") or "").strip()
        if not name:
            continue

        approx = False
        school_name = FOOTBALL_PROVIDER_TO_SCHOOL.get(name)
        if school_name and school_coords.get(school_name):
            lon, lat = school_coords[school_name]
            address = f"{school_name} (home ground)"
            approx = True
        elif name in FOOTBALL_PROVIDER_COORDS:
            lat, lon, address = FOOTBALL_PROVIDER_COORDS[name]
        else:
            unmatched.append(name)
            continue

        props = {
            "name": name,
            "type": row.get("Type"),
            "base": row.get("Address/Base"),
            "address": address,
            "age_range": row.get("Age Range"),
            "notes": row.get("Notes"),
            "approx_location": approx,
        }
        features.append(
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}
        )
    return features, unmatched


VENDOR_DIR = Path(__file__).parent / "vendor"

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Leicester IDACI Deprivation Map</title>
<style>__LEAFLET_CSS__</style>
<script>__LEAFLET_JS__</script>
<style>
  html, body { margin: 0; padding: 0; height: 100%; font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; }
  #app { display: flex; height: 100vh; }
  #sidebar {
    width: 300px; flex-shrink: 0; padding: 16px; box-sizing: border-box;
    background: #fafafa; border-right: 1px solid #ddd; overflow-y: auto;
  }
  #map { flex: 1 1 auto; }
  h1 { font-size: 17px; margin: 0 0 4px; }
  .subtitle { font-size: 12px; color: #555; margin: 0 0 16px; }
  fieldset { border: 1px solid #ddd; border-radius: 6px; margin-bottom: 14px; padding: 10px; }
  .point-group { border-top: 1px solid #e5e5e5; margin-top: 8px; padding-top: 8px; }
  .point-group:first-of-type { border-top: none; margin-top: 4px; padding-top: 0; }
  .subsection-list { margin-top: 6px; padding-left: 18px; border-left: 2px solid #e5e5e5; }
  .subsection-list .legend-row { padding: 2px 0; }
  legend { font-size: 12px; font-weight: 600; color: #333; padding: 0 4px; }
  label { display: block; font-size: 13px; margin: 4px 0; cursor: pointer; }
  select, input[type=text] { width: 100%; padding: 5px; font-size: 13px; box-sizing: border-box; margin-top: 4px; }
  .legend-swatch { display: inline-block; width: 14px; height: 14px; margin-right: 6px; vertical-align: middle; border: 1px solid rgba(0,0,0,0.2); }
  .legend-row { font-size: 12px; margin: 2px 0; display: flex; align-items: center; }
  .stat { font-size: 12px; color: #444; margin: 2px 0; }
  .stat b { color: #111; }
  #summary { font-size: 12px; background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 8px 10px; }
  a { color: #1a5fb4; }
  .leaflet-popup-content { font-size: 13px; line-height: 1.4; min-width: 220px; }
  .leaflet-popup-content h3 { margin: 0 0 6px; font-size: 14px; }
  .leaflet-popup-content table td { padding: 1px 4px; }
  .leaflet-popup-content table td.k { color: #555; }
  footer { font-size: 11px; color: #888; margin-top: 16px; }
</style>
</head>
<body>
<div id="app">
  <div id="sidebar">
    <h1>Leicester IDACI Deprivation Map</h1>
    <p class="subtitle">Income Deprivation Affecting Children Index (IDACI), 2025 Indices of Deprivation &mdash; by Lower-layer Super Output Area (LSOA)</p>

    <fieldset>
      <legend>Shade areas by</legend>
      <label><input type="radio" name="metric" value="idaci_decile" checked> IDACI decile (1 = most deprived children)</label>
      <label><input type="radio" name="metric" value="imd_decile"> Overall IMD decile</label>
      <label><input type="radio" name="metric" value="pct_children_0015"> % of population aged 0&ndash;15</label>
      <label><input type="radio" name="metric" value="pop_0015"> Number of young people aged 0&ndash;15</label>
      <label><input type="radio" name="metric" value="year6_obesity_pct"> Year 6 obesity rate (6 MSOAs only)</label>
    </fieldset>

    <fieldset>
      <legend>Filter by ward</legend>
      <select id="wardFilter"><option value="">All wards</option></select>
      <label style="margin-top:8px;"><input type="checkbox" id="wardBoundariesToggle" checked> Show ward boundaries</label>
    </fieldset>

    <fieldset>
      <legend>Legend</legend>
      <div id="legend"></div>
    </fieldset>

    <div id="summary"></div>

    <fieldset id="pointsFieldset">
      <legend>Points</legend>

      <div class="point-group" id="schoolsGroup">
        <label><input type="checkbox" id="schoolsToggle" checked> Schools (<span id="schoolsCount"></span>)</label>
        <div id="schoolsCategoryLegend" class="subsection-list"></div>
      </div>

      <div class="point-group" id="pitchesGroup" style="display:none;">
        <label><input type="checkbox" id="pitchesToggle" checked> Football pitches (<span id="pitchesCount"></span>)</label>
      </div>

      <div class="point-group" id="footballProvidersGroup" style="display:none;">
        <label><input type="checkbox" id="footballProvidersToggle" checked> Football providers (<span id="footballProvidersCount"></span>)</label>
      </div>

      <div class="point-group" id="youthCentresGroup" style="display:none;">
        <label><input type="checkbox" id="youthCentresToggle" checked> Youth centres (<span id="youthCentresCount"></span>)</label>
      </div>

      <div class="point-group" id="socialMobilityGroup" style="display:none;">
        <label><input type="checkbox" id="socialMobilityToggle" checked> Social mobility partners (<span id="socialMobilityCount"></span>)</label>
      </div>
    </fieldset>

    <footer>
      Source: 2025 English Indices of Deprivation (IDACI sub-domain), ONS mid-2022 population estimates.
      Schools: DfE Get Information about Schools (GIAS) extract.
      Pitches: Leicester playing pitches by site audit; dashed outline = plotted at a matched school's grounds (approximate).
      Year 6 obesity: Leicester City Council NCMP 2023/24 report, 3-year average by MSOA - only the 6 MSOAs the report gives an exact figure for; applied to all LSOAs within that MSOA.
      Deciles: 1 = most deprived 10% of LSOAs nationally, 10 = least deprived.
      Click an area, school or pitch for details.
    </footer>
  </div>
  <div id="map"></div>
</div>

<script>
const DATA = __GEOJSON__;
const DECILE_COLOURS = __DECILE_COLOURS__;
const NO_DATA_COLOUR = "__NO_DATA_COLOUR__";

const map = L.map('map', { scrollWheelZoom: true });
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
}).addTo(map);

let currentMetric = 'idaci_decile';

function colourForDecile(v) {
  if (v === null || v === undefined) return NO_DATA_COLOUR;
  return DECILE_COLOURS[v] || NO_DATA_COLOUR;
}

function colourForRange(v, min, max) {
  if (v === null || v === undefined) return NO_DATA_COLOUR;
  const t = max > min ? (v - min) / (max - min) : 0;
  // pale yellow (low) -> dark red (high)
  const stops = ["#ffffe5", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#a50f15", "#67000d"];
  const idx = Math.min(stops.length - 1, Math.floor(t * stops.length));
  return stops[idx];
}

const CONTINUOUS_METRICS = ['pct_children_0015', 'pop_0015', 'year6_obesity_pct'];

function computeRange(prop) {
  const vals = DATA.features.map(f => f.properties[prop]).filter(v => v !== null && v !== undefined);
  return vals.length ? { min: Math.min(...vals), max: Math.max(...vals) } : { min: 0, max: 100 };
}

let pctRange = computeRange('pct_children_0015');
let pop0015Range = computeRange('pop_0015');
let obesityRange = computeRange('year6_obesity_pct');

function styleFor(feature) {
  const p = feature.properties;
  let fill;
  if (currentMetric === 'pct_children_0015') {
    fill = colourForRange(p.pct_children_0015, pctRange.min, pctRange.max);
  } else if (currentMetric === 'pop_0015') {
    fill = colourForRange(p.pop_0015, pop0015Range.min, pop0015Range.max);
  } else if (currentMetric === 'year6_obesity_pct') {
    fill = colourForRange(p.year6_obesity_pct, obesityRange.min, obesityRange.max);
  } else {
    fill = colourForDecile(p[currentMetric]);
  }
  return { fillColor: fill, weight: 1, color: '#555', fillOpacity: 0.75 };
}

function fmt(v, suffix) {
  if (v === null || v === undefined) return 'no data';
  return v + (suffix || '');
}

function popupHtml(p) {
  return `
    <h3>${p.lsoa_name || p.lsoa_code}</h3>
    <table>
      <tr><td class="k">Ward</td><td>${p.ward || '&ndash;'}</td></tr>
      <tr><td class="k">MSOA</td><td>${p.msoa_name || '&ndash;'}</td></tr>
      <tr><td class="k">IDACI score (% children income-deprived)</td><td>${fmt(p.idaci_score, '%')}</td></tr>
      <tr><td class="k">IDACI decile</td><td>${fmt(p.idaci_decile)} (1=most deprived)</td></tr>
      <tr><td class="k">IDACI national percentile</td><td>${p.idaci_percentile !== null && p.idaci_percentile !== undefined ? 'Most deprived ' + p.idaci_percentile + '% nationally' : 'no data'}</td></tr>
      <tr><td class="k">Overall IMD decile</td><td>${fmt(p.imd_decile)}</td></tr>
      <tr><td class="k">Population 0&ndash;15</td><td>${fmt(p.pop_0015)} (${fmt(p.pct_children_0015, '%')} of area)</td></tr>
      <tr><td class="k">Total population</td><td>${fmt(p.pop_total)}</td></tr>
      ${p.year6_obesity_pct !== null && p.year6_obesity_pct !== undefined ? `<tr><td class="k">Year 6 obesity rate (MSOA, NCMP 23/24)</td><td>${p.year6_obesity_pct}%</td></tr>` : ''}
    </table>
  `;
}

let geoLayer;
let currentWard = '';

function passesFilter(feature) {
  return !currentWard || feature.properties.ward === currentWard;
}

function rebuildLayer() {
  if (geoLayer) map.removeLayer(geoLayer);
  geoLayer = L.geoJSON(DATA, {
    filter: passesFilter,
    style: styleFor,
    onEachFeature: (feature, layer) => {
      layer.bindPopup(popupHtml(feature.properties));
      layer.on({
        mouseover: e => e.target.setStyle({ weight: 2.5, color: '#000' }),
        mouseout: e => geoLayer.resetStyle(e.target),
      });
    }
  }).addTo(map);
  updateSummary();
}

function updateSummary() {
  const shown = DATA.features.filter(passesFilter);
  const scores = shown.map(f => f.properties.idaci_score).filter(v => v !== null && v !== undefined);
  const avg = scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length) : null;
  const mostDeprived = shown
    .filter(f => f.properties.idaci_decile !== null && f.properties.idaci_decile <= 3)
    .length;
  document.getElementById('summary').innerHTML = `
    <div class="stat"><b>${shown.length}</b> LSOAs shown${currentWard ? ' in ' + currentWard : ''}</div>
    <div class="stat">Average IDACI score: <b>${avg !== null ? avg.toFixed(1) + '%' : 'n/a'}</b></div>
    <div class="stat"><b>${mostDeprived}</b> areas in the most deprived 30% nationally for child income deprivation</div>
  `;
}

function renderLegend() {
  const el = document.getElementById('legend');
  if (currentMetric === 'pct_children_0015') {
    el.innerHTML = `
      <div class="legend-row"><span class="legend-swatch" style="background:#ffffe5"></span>${pctRange.min.toFixed(0)}% (fewest children)</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#fb6a4a"></span>mid-range</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#67000d"></span>${pctRange.max.toFixed(0)}% (most children)</div>
    `;
    return;
  }
  if (currentMetric === 'pop_0015') {
    el.innerHTML = `
      <div class="legend-row"><span class="legend-swatch" style="background:#ffffe5"></span>${pop0015Range.min} young people (fewest)</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#fb6a4a"></span>mid-range</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#67000d"></span>${pop0015Range.max} young people (most)</div>
    `;
    return;
  }
  if (currentMetric === 'year6_obesity_pct') {
    el.innerHTML = `
      <div class="legend-row"><span class="legend-swatch" style="background:#ffffe5"></span>${obesityRange.min}% (lowest of the 6)</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#fb6a4a"></span>mid-range</div>
      <div class="legend-row"><span class="legend-swatch" style="background:#67000d"></span>${obesityRange.max}% (highest of the 6)</div>
      <div class="legend-row"><span class="legend-swatch" style="background:${NO_DATA_COLOUR}"></span>No data (32 of 38 MSOAs)</div>
    `;
    return;
  }
  let rows = '';
  for (let d = 1; d <= 10; d++) {
    rows += `<div class="legend-row"><span class="legend-swatch" style="background:${DECILE_COLOURS[d]}"></span>Decile ${d}${d === 1 ? ' (most deprived)' : ''}${d === 10 ? ' (least deprived)' : ''}</div>`;
  }
  rows += `<div class="legend-row"><span class="legend-swatch" style="background:${NO_DATA_COLOUR}"></span>No data</div>`;
  el.innerHTML = rows;
}

document.querySelectorAll('input[name=metric]').forEach(el => {
  el.addEventListener('change', e => {
    currentMetric = e.target.value;
    renderLegend();
    rebuildLayer();
  });
});

const wardSelect = document.getElementById('wardFilter');
(function populateWards() {
  const wards = Array.from(new Set(DATA.features.map(f => f.properties.ward).filter(Boolean))).sort();
  wards.forEach(w => {
    const opt = document.createElement('option');
    opt.value = w;
    opt.textContent = w;
    wardSelect.appendChild(opt);
  });
})();
wardSelect.addEventListener('change', e => {
  currentWard = e.target.value;
  rebuildLayer();
  if (map.hasLayer(wardBoundaryLayer)) wardBoundaryLayer.bringToFront();
  rebuildSchoolLayer();
  rebuildPitchLayer();
  if (currentWard) {
    const bounds = L.geoJSON(DATA.features.filter(passesFilter)).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] });
  } else {
    map.fitBounds(L.geoJSON(DATA).getBounds());
  }
});

// LSOA polygons must be added to the map before the schools/pitches marker
// layers below, so those markers end up on top of the polygons in the SVG
// paint order and can still receive clicks (otherwise the polygon - even
// semi-transparent - swallows the click).
renderLegend();
rebuildLayer();
map.fitBounds(L.geoJSON(DATA).getBounds());

// ---- Ward boundaries overlay ----
// Outline-only, non-interactive (interactive: false) so it sits visually on
// top of the LSOA choropleth without swallowing clicks meant for it.
const WARD_BOUNDARIES = __WARD_BOUNDARIES_GEOJSON__;
let wardBoundaryLayer = L.geoJSON(WARD_BOUNDARIES, {
  interactive: false,
  style: { color: '#1a1a1a', weight: 2.5, opacity: 0.85, fill: false },
});
if (WARD_BOUNDARIES.features.length) {
  wardBoundaryLayer.addTo(map);
}
document.getElementById('wardBoundariesToggle').addEventListener('change', e => {
  if (e.target.checked) wardBoundaryLayer.addTo(map);
  else map.removeLayer(wardBoundaryLayer);
});

// ---- Schools overlay ----
const SCHOOLS = __SCHOOLS_GEOJSON__;
const SCHOOL_CATEGORY_COLOURS = __SCHOOL_CATEGORY_COLOURS__;
const SCHOOL_DEFAULT_COLOUR = "__SCHOOL_DEFAULT_COLOUR__";
let schoolLayer = L.layerGroup();
let schoolsVisible = true;
const activeSchoolCategories = new Set();

function schoolColour(category) {
  return SCHOOL_CATEGORY_COLOURS[category] || SCHOOL_DEFAULT_COLOUR;
}

function schoolPassesFilter(feature) {
  const p = feature.properties;
  if (currentWard && p.ward !== currentWard) return false;
  return activeSchoolCategories.has(p.category);
}

function ageRange(p) {
  if (p.age_low === null && p.age_high === null) return null;
  return `${p.age_low ?? '?'}–${p.age_high ?? '?'}`;
}

function schoolPopupHtml(p) {
  const age = ageRange(p);
  return `
    <h3>${p.name}</h3>
    <table>
      <tr><td class="k">Category</td><td>${p.category || '&ndash;'}</td></tr>
      <tr><td class="k">Type</td><td>${p.type || '&ndash;'}</td></tr>
      <tr><td class="k">Phase</td><td>${p.phase || '&ndash;'}${age ? ' (ages ' + age + ')' : ''}</td></tr>
      <tr><td class="k">Ward</td><td>${p.ward || '&ndash;'}</td></tr>
      <tr><td class="k">Address</td><td>${p.address || '&ndash;'}</td></tr>
      ${p.pupils !== null && p.pupils !== undefined ? `<tr><td class="k">Pupils on roll</td><td>${p.pupils}</td></tr>` : ''}
      ${p.pct_fsm !== null && p.pct_fsm !== undefined ? `<tr><td class="k">% free school meals</td><td>${p.pct_fsm}%</td></tr>` : ''}
      ${p.website ? `<tr><td class="k">Website</td><td><a href="${p.website}" target="_blank" rel="noopener">${p.website}</a></td></tr>` : ''}
    </table>
  `;
}

function rebuildSchoolLayer() {
  schoolLayer.clearLayers();
  if (!schoolsVisible) return;
  SCHOOLS.features.filter(schoolPassesFilter).forEach(feature => {
    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      radius: 6,
      weight: 1,
      color: '#222',
      fillColor: schoolColour(feature.properties.category),
      fillOpacity: 0.9,
    });
    marker.bindPopup(schoolPopupHtml(feature.properties));
    marker.addTo(schoolLayer);
  });
}

function renderSchoolCategoryLegend() {
  const counts = {};
  SCHOOLS.features.forEach(f => {
    counts[f.properties.category] = (counts[f.properties.category] || 0) + 1;
  });
  const categories = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  categories.forEach(c => activeSchoolCategories.add(c));
  const el = document.getElementById('schoolsCategoryLegend');
  el.innerHTML = categories.map(c => `
    <label class="legend-row" style="cursor:pointer;">
      <input type="checkbox" class="school-cat-cb" data-cat="${c}" checked style="margin-right:6px;">
      <span class="legend-swatch" style="background:${schoolColour(c)}"></span>${c} (${counts[c]})
    </label>
  `).join('');
  document.getElementById('schoolsCount').textContent = SCHOOLS.features.length;
  el.querySelectorAll('.school-cat-cb').forEach(cb => {
    cb.addEventListener('change', e => {
      const cat = e.target.dataset.cat;
      if (e.target.checked) activeSchoolCategories.add(cat);
      else activeSchoolCategories.delete(cat);
      rebuildSchoolLayer();
    });
  });
}

document.getElementById('schoolsToggle').addEventListener('change', e => {
  schoolsVisible = e.target.checked;
  if (schoolsVisible) schoolLayer.addTo(map);
  else map.removeLayer(schoolLayer);
  rebuildSchoolLayer();
});

if (SCHOOLS.features.length) {
  renderSchoolCategoryLegend();
  rebuildSchoolLayer();
  schoolLayer.addTo(map);
} else {
  document.getElementById('schoolsGroup').style.display = 'none';
}

// ---- Football pitches overlay (Leicester playing-pitches-by-site audit) ----
const PITCHES = __PITCHES_GEOJSON__;
let pitchLayer = L.layerGroup();
let pitchesVisible = true;

function pitchPassesFilter(feature) {
  return true;
}

function pitchPopupHtml(p) {
  const types = Object.entries(p.pitch_types || {}).map(([t, n]) => `${n}&times; ${t}`).join(', ');
  return `
    <h3>${p.name}</h3>
    <table>
      ${p.sub_area ? `<tr><td class="k">Area</td><td>${p.sub_area}</td></tr>` : ''}
      ${p.access ? `<tr><td class="k">Access</td><td>${p.access}</td></tr>` : ''}
      <tr><td class="k">Pitches</td><td>${p.total_pitches} (capacity ${p.total_capacity} teams)</td></tr>
      ${types ? `<tr><td class="k">Pitch types</td><td>${types}</td></tr>` : ''}
      ${p.has_3g ? `<tr><td class="k">3G / AGP</td><td>Yes</td></tr>` : ''}
      ${p.approx_location ? `<tr><td class="k">Location</td><td>Approximate - plotted at ${p.matched_school}</td></tr>` : ''}
    </table>
  `;
}

function rebuildPitchLayer() {
  pitchLayer.clearLayers();
  if (!pitchesVisible) return;
  PITCHES.features.filter(pitchPassesFilter).forEach(feature => {
    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      radius: 6,
      weight: 1,
      color: '#0b5d1e',
      fillColor: feature.properties.has_3g ? '#1f9e4d' : '#7cd992',
      fillOpacity: 0.9,
      dashArray: feature.properties.approx_location ? '2,2' : null,
    });
    marker.bindPopup(pitchPopupHtml(feature.properties));
    marker.addTo(pitchLayer);
  });
}

document.getElementById('pitchesToggle').addEventListener('change', e => {
  pitchesVisible = e.target.checked;
  if (pitchesVisible) pitchLayer.addTo(map);
  else map.removeLayer(pitchLayer);
  rebuildPitchLayer();
});

if (PITCHES.features.length) {
  document.getElementById('pitchesGroup').style.display = '';
  document.getElementById('pitchesCount').textContent = PITCHES.features.length;
  rebuildPitchLayer();
  pitchLayer.addTo(map);
}

// ---- Football providers overlay ----
const FOOTBALL_PROVIDERS = __FOOTBALL_PROVIDERS_GEOJSON__;
let footballProviderLayer = L.layerGroup();
let footballProvidersVisible = true;

function footballProviderPopupHtml(p) {
  return `
    <h3>${p.name}</h3>
    <table>
      ${p.type ? `<tr><td class="k">Type</td><td>${p.type}</td></tr>` : ''}
      ${p.address ? `<tr><td class="k">Address</td><td>${p.address}</td></tr>` : ''}
      ${p.age_range ? `<tr><td class="k">Age range</td><td>${p.age_range}</td></tr>` : ''}
      ${p.notes ? `<tr><td class="k">Notes</td><td>${p.notes}</td></tr>` : ''}
      ${p.approx_location ? `<tr><td class="k">Location</td><td>Approximate - plotted at home ground</td></tr>` : ''}
    </table>
  `;
}

function rebuildFootballProviderLayer() {
  footballProviderLayer.clearLayers();
  if (!footballProvidersVisible) return;
  FOOTBALL_PROVIDERS.features.forEach(feature => {
    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      radius: 7,
      weight: 1.5,
      color: '#004d40',
      fillColor: '#26a69a',
      fillOpacity: 0.9,
      dashArray: feature.properties.approx_location ? '2,2' : null,
    });
    marker.bindPopup(footballProviderPopupHtml(feature.properties));
    marker.addTo(footballProviderLayer);
  });
}

document.getElementById('footballProvidersToggle').addEventListener('change', e => {
  footballProvidersVisible = e.target.checked;
  if (footballProvidersVisible) footballProviderLayer.addTo(map);
  else map.removeLayer(footballProviderLayer);
  rebuildFootballProviderLayer();
});

if (FOOTBALL_PROVIDERS.features.length) {
  document.getElementById('footballProvidersGroup').style.display = '';
  document.getElementById('footballProvidersCount').textContent = FOOTBALL_PROVIDERS.features.length;
  rebuildFootballProviderLayer();
  footballProviderLayer.addTo(map);
}

// ---- Youth centres overlay ----
const YOUTH_CENTRES = __YOUTH_CENTRES_GEOJSON__;
let youthCentreLayer = L.layerGroup();
let youthCentresVisible = true;

function youthCentrePassesFilter(feature) {
  return true;
}

function youthCentrePopupHtml(p) {
  return `
    <h3>${p.name} Youth Centre</h3>
    <table>
      ${p.address ? `<tr><td class="k">Address</td><td>${p.address}, ${p.postcode || ''}</td></tr>` : ''}
      ${p.ward ? `<tr><td class="k">Ward</td><td>${p.ward}</td></tr>` : ''}
      ${p.age_range ? `<tr><td class="k">Age range</td><td>${p.age_range}</td></tr>` : ''}
      ${p.session_days ? `<tr><td class="k">Sessions</td><td>${p.session_days}</td></tr>` : ''}
      ${p.focus ? `<tr><td class="k">Focus</td><td>${p.focus}</td></tr>` : ''}
      ${p.approx_location ? `<tr><td class="k">Location</td><td>Approximate (ward-level - postcode not found)</td></tr>` : ''}
    </table>
  `;
}

function rebuildYouthCentreLayer() {
  youthCentreLayer.clearLayers();
  if (!youthCentresVisible) return;
  YOUTH_CENTRES.features.filter(youthCentrePassesFilter).forEach(feature => {
    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      radius: 7,
      weight: 1.5,
      color: '#880e4f',
      fillColor: '#f06292',
      fillOpacity: 0.9,
      dashArray: feature.properties.approx_location ? '2,2' : null,
    });
    marker.bindPopup(youthCentrePopupHtml(feature.properties));
    marker.addTo(youthCentreLayer);
  });
}

document.getElementById('youthCentresToggle').addEventListener('change', e => {
  youthCentresVisible = e.target.checked;
  if (youthCentresVisible) youthCentreLayer.addTo(map);
  else map.removeLayer(youthCentreLayer);
  rebuildYouthCentreLayer();
});

if (YOUTH_CENTRES.features.length) {
  document.getElementById('youthCentresGroup').style.display = '';
  document.getElementById('youthCentresCount').textContent = YOUTH_CENTRES.features.length;
  rebuildYouthCentreLayer();
  youthCentreLayer.addTo(map);
}

// ---- Social mobility partners overlay ----
const SOCIAL_MOBILITY_PARTNERS = __SOCIAL_MOBILITY_PARTNERS_GEOJSON__;
let socialMobilityLayer = L.layerGroup();
let socialMobilityVisible = true;

function socialMobilityPopupHtml(p) {
  return `
    <h3>${p.name}</h3>
    <table>
      ${p.type ? `<tr><td class="k">Type</td><td>${p.type}</td></tr>` : ''}
      ${p.address ? `<tr><td class="k">Address</td><td>${p.address}</td></tr>` : ''}
      ${p.scope ? `<tr><td class="k">Geographic scope</td><td>${p.scope}</td></tr>` : ''}
      ${p.focus ? `<tr><td class="k">Focus</td><td>${p.focus}</td></tr>` : ''}
      ${p.notes ? `<tr><td class="k">Notes</td><td>${p.notes}</td></tr>` : ''}
    </table>
  `;
}

function rebuildSocialMobilityLayer() {
  socialMobilityLayer.clearLayers();
  if (!socialMobilityVisible) return;
  SOCIAL_MOBILITY_PARTNERS.features.forEach(feature => {
    const [lon, lat] = feature.geometry.coordinates;
    const marker = L.circleMarker([lat, lon], {
      radius: 7,
      weight: 1.5,
      color: '#4a148c',
      fillColor: '#9c64d8',
      fillOpacity: 0.9,
    });
    marker.bindPopup(socialMobilityPopupHtml(feature.properties));
    marker.addTo(socialMobilityLayer);
  });
}

document.getElementById('socialMobilityToggle').addEventListener('change', e => {
  socialMobilityVisible = e.target.checked;
  if (socialMobilityVisible) socialMobilityLayer.addTo(map);
  else map.removeLayer(socialMobilityLayer);
  rebuildSocialMobilityLayer();
});

if (SOCIAL_MOBILITY_PARTNERS.features.length) {
  document.getElementById('socialMobilityGroup').style.display = '';
  document.getElementById('socialMobilityCount').textContent = SOCIAL_MOBILITY_PARTNERS.features.length;
  rebuildSocialMobilityLayer();
  socialMobilityLayer.addTo(map);
}

</script>
</body>
</html>
"""


def build_html(
    features,
    school_features=None,
    pitch_features=None,
    youth_centre_features=None,
    social_mobility_partner_features=None,
    ward_boundary_features=None,
    football_provider_features=None,
):
    geojson = {"type": "FeatureCollection", "features": features}
    schools_geojson = {"type": "FeatureCollection", "features": school_features or []}
    pitches_geojson = {"type": "FeatureCollection", "features": pitch_features or []}
    youth_centres_geojson = {"type": "FeatureCollection", "features": youth_centre_features or []}
    social_mobility_geojson = {"type": "FeatureCollection", "features": social_mobility_partner_features or []}
    ward_boundaries_geojson = {"type": "FeatureCollection", "features": ward_boundary_features or []}
    football_providers_geojson = {"type": "FeatureCollection", "features": football_provider_features or []}
    leaflet_js = (VENDOR_DIR / "leaflet.js").read_text(encoding="utf-8")
    leaflet_css = (VENDOR_DIR / "leaflet.css").read_text(encoding="utf-8")
    html = HTML_TEMPLATE.replace("__GEOJSON__", json.dumps(geojson))
    html = html.replace("__DECILE_COLOURS__", json.dumps(DECILE_COLOURS))
    html = html.replace("__NO_DATA_COLOUR__", NO_DATA_COLOUR)
    html = html.replace("__LEAFLET_JS__", leaflet_js)
    html = html.replace("__LEAFLET_CSS__", leaflet_css)
    html = html.replace("__SCHOOLS_GEOJSON__", json.dumps(schools_geojson))
    html = html.replace("__SCHOOL_CATEGORY_COLOURS__", json.dumps(SCHOOL_CATEGORY_COLOURS))
    html = html.replace("__SCHOOL_DEFAULT_COLOUR__", SCHOOL_DEFAULT_COLOUR)
    html = html.replace("__PITCHES_GEOJSON__", json.dumps(pitches_geojson))
    html = html.replace("__YOUTH_CENTRES_GEOJSON__", json.dumps(youth_centres_geojson))
    html = html.replace("__SOCIAL_MOBILITY_PARTNERS_GEOJSON__", json.dumps(social_mobility_geojson))
    html = html.replace("__WARD_BOUNDARIES_GEOJSON__", json.dumps(ward_boundaries_geojson))
    html = html.replace("__FOOTBALL_PROVIDERS_GEOJSON__", json.dumps(football_providers_geojson))
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Deprivation-in-Leicester CSV")
    parser.add_argument("--schools", type=Path, help="GIAS (Get Information about Schools) establishment export CSV")
    parser.add_argument(
        "--pitches",
        type=Path,
        help="Football pitches: either a by-site inventory CSV (Site Name, Pitch Type, ...) "
        "matched against --schools by name, or a GeoJSON with coordinates already present",
    )
    parser.add_argument(
        "--youth-centres",
        type=Path,
        help="Youth centres CSV (Youth Centre, Address, Postcode, ...) matched to coordinates by postcode",
    )
    parser.add_argument(
        "--social-mobility-partners",
        type=Path,
        help="Social mobility partners CSV (Organisation, Type, Geographic Scope, ...) matched by organisation name",
    )
    parser.add_argument(
        "--football-providers",
        type=Path,
        help="Football providers CSV (Organisation, Type, Address/Base, ...) matched by organisation name",
    )
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    rows = list(load_rows(args.input))
    features = build_features(rows)
    if not features:
        raise SystemExit("No features parsed from input CSV - check the file format.")

    school_features = []
    if args.schools:
        school_rows = list(load_school_rows(args.schools))
        school_features = build_school_features(school_rows)

    pitch_features = []
    unmatched_sites = []
    if args.pitches:
        if args.pitches.suffix.lower() == ".csv":
            pitch_rows = list(load_pitch_inventory_rows(args.pitches))
            pitch_features, unmatched_sites = build_pitch_features_from_inventory(pitch_rows, school_features)
        else:
            pitch_geojson = json.loads(args.pitches.read_text(encoding="utf-8"))
            pitch_features = pitch_geojson.get("features", [])

    youth_centre_features = []
    unmatched_youth_centres = []
    if args.youth_centres:
        youth_centre_rows = list(load_youth_centre_rows(args.youth_centres))
        youth_centre_features, unmatched_youth_centres = build_youth_centre_features(youth_centre_rows)

    social_mobility_features = []
    unmatched_partners = []
    if args.social_mobility_partners:
        partner_rows = list(load_social_mobility_partner_rows(args.social_mobility_partners))
        social_mobility_features, unmatched_partners = build_social_mobility_partner_features(partner_rows)

    football_provider_features = []
    unmatched_providers = []
    if args.football_providers:
        provider_rows = list(load_football_provider_rows(args.football_providers))
        football_provider_features, unmatched_providers = build_football_provider_features(
            provider_rows, school_features
        )

    ward_boundary_features = build_ward_boundaries(features)

    html = build_html(
        features,
        school_features,
        pitch_features,
        youth_centre_features,
        social_mobility_features,
        ward_boundary_features,
        football_provider_features,
    )
    out_html = args.outdir / "leicester_idaci_interactive_map.html"
    out_html.write_text(html, encoding="utf-8")

    out_geojson = args.outdir / "leicester_idaci_lsoa.geojson"
    out_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=None),
        encoding="utf-8",
    )

    print(f"Parsed {len(features)} LSOAs")
    out_wards = args.outdir / "leicester_ward_boundaries.geojson"
    out_wards.write_text(
        json.dumps({"type": "FeatureCollection", "features": ward_boundary_features}, indent=None),
        encoding="utf-8",
    )
    print(f"Dissolved {len(ward_boundary_features)} ward boundaries")
    print(f"Wrote {out_wards}")
    if school_features:
        out_schools = args.outdir / "leicester_schools.geojson"
        out_schools.write_text(
            json.dumps({"type": "FeatureCollection", "features": school_features}, indent=None),
            encoding="utf-8",
        )
        print(f"Parsed {len(school_features)} schools")
        print(f"Wrote {out_schools}")
    if pitch_features:
        out_pitches = args.outdir / "leicester_pitches.geojson"
        out_pitches.write_text(
            json.dumps({"type": "FeatureCollection", "features": pitch_features}, indent=None),
            encoding="utf-8",
        )
        print(f"Parsed {len(pitch_features)} pitch sites")
        print(f"Wrote {out_pitches}")
    if unmatched_sites:
        print(f"Warning: {len(unmatched_sites)} pitch sites have no known location and were skipped:")
        for s in unmatched_sites:
            print(f"  - {s}")
    if youth_centre_features:
        out_youth = args.outdir / "leicester_youth_centres.geojson"
        out_youth.write_text(
            json.dumps({"type": "FeatureCollection", "features": youth_centre_features}, indent=None),
            encoding="utf-8",
        )
        print(f"Parsed {len(youth_centre_features)} youth centres")
        print(f"Wrote {out_youth}")
    if unmatched_youth_centres:
        print(f"Warning: {len(unmatched_youth_centres)} youth centres have no known location and were skipped:")
        for s in unmatched_youth_centres:
            print(f"  - {s}")
    if social_mobility_features:
        out_partners = args.outdir / "leicester_social_mobility_partners.geojson"
        out_partners.write_text(
            json.dumps({"type": "FeatureCollection", "features": social_mobility_features}, indent=None),
            encoding="utf-8",
        )
        print(f"Parsed {len(social_mobility_features)} social mobility partners")
        print(f"Wrote {out_partners}")
    if unmatched_partners:
        print(f"Warning: {len(unmatched_partners)} social mobility partners have no known location and were skipped:")
        for s in unmatched_partners:
            print(f"  - {s}")
    if football_provider_features:
        out_providers = args.outdir / "leicester_football_providers.geojson"
        out_providers.write_text(
            json.dumps({"type": "FeatureCollection", "features": football_provider_features}, indent=None),
            encoding="utf-8",
        )
        print(f"Parsed {len(football_provider_features)} football providers")
        print(f"Wrote {out_providers}")
    if unmatched_providers:
        print(f"Warning: {len(unmatched_providers)} football providers have no known location and were skipped:")
        for s in unmatched_providers:
            print(f"  - {s}")
    print(f"Wrote {out_html}")
    print(f"Wrote {out_geojson}")


if __name__ == "__main__":
    main()
