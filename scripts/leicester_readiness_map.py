#!/usr/bin/env python3
"""
Build an interactive HTML map of Leicester combining:

  1. IDACI (Income Deprivation Affecting Children Index, IoD 2019) choropleth
     per LSOA, and
  2. an "infrastructure readiness" score (0-1) per LSOA, derived from
     OpenStreetMap sports/leisure facilities, plus the facility points
     themselves as a toggleable layer.

Data is downloaded on first run and cached in --data-dir, so later runs work
offline:

  * IDACI scores  - English Indices of Deprivation 2019, File 7 (gov.uk),
                    filtered to Leicester and cached as CSV.
  * Boundaries    - LSOA (Dec 2011) generalised boundaries from the ONS Open
                    Geography Portal ArcGIS API, filtered to Leicester.
  * Facilities    - Overpass API (OpenStreetMap): pitches, sports centres /
                    halls, parks / recreation grounds around Leicester.

Any of the three inputs can instead be supplied as a local file with
--idaci / --boundaries / --facilities (same expectations as the cached files).

Readiness score methodology (assumptions - tune via CLI flags):
  For each LSOA, facilities are counted within a walkable buffer of the LSOA
  polygon (800 m for pitches and parks, 1200 m for sports centres). Each
  component count is scaled to 0-1 against the city-wide 90th percentile
  (capped at 1), then combined as a weighted sum:
  0.5 * pitches + 0.3 * sports centres + 0.2 * parks.

Outputs (in --outdir):
  leicester_idaci_readiness_map.html  - interactive folium/Leaflet map
  leicester_readiness_lsoa.csv        - per-LSOA table of all scores/counts
"""
import argparse
import json
import sys
from pathlib import Path

import branca.colormap as cm
import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from idaci_leicester_map import LEICESTER_LAD_CODE, load_boundaries, load_idaci

IDACI_URL = (
    "https://assets.publishing.service.gov.uk/media/5d8b3abded915d0373d3540f/"
    "File_7_-_All_IoD2019_Scores__Ranks__Deciles_and_Population_Denominators_3.csv"
)
ONS_LSOA_FEATURESERVER = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Lower_layer_Super_Output_Areas_Dec_2011_Boundaries_Generalised_Clipped_BGC_EW_V3/"
    "FeatureServer/0/query"
)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM leisure tags per readiness component.
FACILITY_CATEGORIES = {
    "pitch": ("pitch",),
    "sports_centre": ("sports_centre", "sports_hall"),
    "park": ("park", "recreation_ground"),
}
CATEGORY_LABELS = {
    "pitch": "Pitches",
    "sports_centre": "Sports centres & halls",
    "park": "Parks & recreation grounds",
}
CATEGORY_COLOURS = {"pitch": "#1d6fb8", "sports_centre": "#8039a8", "park": "#1a7a3c"}


def fetch_idaci(data_dir):
    """Return the Leicester IDACI table, downloading gov.uk File 7 if not cached."""
    cache = data_dir / "leicester_idaci_2019.csv"
    if not cache.exists():
        print(f"Downloading IDACI scores (IoD 2019 File 7) from gov.uk ...")
        df = pd.read_csv(IDACI_URL)
        lad_col = next(c for c in df.columns if "local authority district code" in c.lower())
        df = df[df[lad_col] == LEICESTER_LAD_CODE]
        if df.empty:
            raise SystemExit("No Leicester rows found in the IoD 2019 file.")
        df.to_csv(cache, index=False)
        print(f"Cached {len(df)} Leicester rows to {cache}")
    return load_idaci(cache)


def fetch_boundaries(data_dir):
    """Return Leicester LSOA boundaries, downloading from the ONS API if not cached."""
    cache = data_dir / "leicester_lsoa_boundaries.geojson"
    if not cache.exists():
        print("Downloading Leicester LSOA boundaries from ONS Open Geography Portal ...")
        features = []
        offset = 0
        while True:
            resp = requests.get(
                ONS_LSOA_FEATURESERVER,
                params={
                    # Leicester LSOA names are "Leicester 001A" etc.; Leicestershire
                    # districts use their own names, so this filter is unambiguous.
                    "where": "LSOA11NM LIKE 'Leicester 0%'",
                    "outFields": "LSOA11CD,LSOA11NM",
                    "outSR": "4326",
                    "f": "geojson",
                    "resultOffset": offset,
                },
                timeout=120,
            )
            resp.raise_for_status()
            page = resp.json()
            features.extend(page.get("features", []))
            if not page.get("properties", {}).get("exceededTransferLimit"):
                break
            offset = len(features)
        if not features:
            raise SystemExit("ONS API returned no Leicester LSOA boundaries.")
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        print(f"Cached {len(features)} LSOA boundaries to {cache}")
    return load_boundaries(cache)


def fetch_facilities(data_dir, boundaries):
    """Return OSM facility points (categorised), downloading via Overpass if not cached.

    Queries a bounding box 1 km beyond Leicester so facilities just outside the
    city boundary still count towards nearby LSOAs.
    """
    cache = data_dir / "leicester_osm_facilities.geojson"
    if not cache.exists():
        minx, miny, maxx, maxy = (
            boundaries.to_crs(epsg=27700).buffer(1000).to_crs(epsg=4326).total_bounds
        )
        leisure_values = "|".join(v for tags in FACILITY_CATEGORIES.values() for v in tags)
        query = (
            f"[out:json][timeout:180];"
            f'nwr["leisure"~"^({leisure_values})$"]({miny},{minx},{maxy},{maxx});'
            f"out center tags;"
        )
        print("Downloading facilities from OpenStreetMap (Overpass API) ...")
        resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=240)
        resp.raise_for_status()
        records = []
        for el in resp.json()["elements"]:
            lon = el.get("lon") or el.get("center", {}).get("lon")
            lat = el.get("lat") or el.get("center", {}).get("lat")
            tags = el.get("tags", {})
            leisure = tags.get("leisure")
            category = next(
                (cat for cat, values in FACILITY_CATEGORIES.items() if leisure in values), None
            )
            if lon is None or category is None:
                continue
            records.append(
                {
                    "category": category,
                    "leisure": leisure,
                    "sport": tags.get("sport", ""),
                    "name": tags.get("name", ""),
                    "geometry": gpd.points_from_xy([lon], [lat])[0],
                }
            )
        if not records:
            raise SystemExit("Overpass returned no facilities for the Leicester area.")
        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf.to_file(cache, driver="GeoJSON")
        print(f"Cached {len(gdf)} facilities to {cache}")
    gdf = gpd.read_file(cache)
    if "category" not in gdf.columns:
        raise SystemExit(f"{cache} must have a 'category' property (pitch/sports_centre/park).")
    return gdf


def compute_readiness(boundaries, facilities, buffers, weights):
    """Add per-category facility counts and a 0-1 readiness score to `boundaries`.

    Counts facilities within `buffers[category]` metres of each LSOA polygon,
    scales each count against the city-wide 90th percentile (capped at 1) and
    combines the components with `weights` (normalised to sum to 1).
    """
    lsoas = boundaries.to_crs(epsg=27700)
    points = facilities.to_crs(epsg=27700)

    total_weight = sum(weights.values())
    score = np.zeros(len(lsoas))
    for category in FACILITY_CATEGORIES:
        buffered = lsoas.geometry.buffer(buffers[category])
        cat_points = points[points["category"] == category]
        counts = np.array(
            [len(cat_points[cat_points.within(geom)]) for geom in buffered]
        )
        boundaries[f"n_{category}"] = counts
        cap = max(np.percentile(counts, 90), 1)
        boundaries[f"score_{category}"] = np.clip(counts / cap, 0, 1).round(3)
        score += (weights[category] / total_weight) * boundaries[f"score_{category}"]
    boundaries["readiness_score"] = score.round(3)
    return boundaries


def build_map(merged, facilities, out_html):
    center = merged.geometry.union_all().centroid
    m = folium.Map(location=[center.y, center.x], zoom_start=12, tiles="cartodbpositron")

    idaci_values = merged["idaci_score"].dropna()
    idaci_cmap = cm.linear.OrRd_09.scale(float(idaci_values.min()), float(idaci_values.max()))
    idaci_cmap.caption = "IDACI score (proportion of children 0-15 in income-deprived families)"
    readiness_cmap = cm.LinearColormap(
        ["#d73027", "#fee08b", "#1a9850"], vmin=0, vmax=1,
        caption="Infrastructure readiness (0 = least served, 1 = best served)",
    )

    tooltip_fields = [
        "lsoa_code", "lsoa_name", "idaci_score", "readiness_score",
        "n_pitch", "n_sports_centre", "n_park",
    ]
    tooltip_aliases = [
        "LSOA code", "LSOA name", "IDACI score", "Readiness (0-1)",
        "Pitches within 800 m", "Sports centres within 1200 m", "Parks within 800 m",
    ]
    if "lsoa_name" not in merged.columns:
        merged["lsoa_name"] = ""
    merged["idaci_score"] = merged["idaci_score"].round(3)

    def choropleth(column, colormap, name, show):
        def style(feature):
            value = feature["properties"][column]
            no_data = value is None or (isinstance(value, float) and np.isnan(value))
            fill = "#bdbdbd" if no_data else colormap(value)
            return {"fillColor": fill, "fillOpacity": 0.75, "color": "#555", "weight": 0.5}

        layer = folium.GeoJson(
            merged,
            name=name,
            style_function=style,
            show=show,
            highlight_function=lambda f: {"weight": 2.5, "color": "#000"},
            tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases),
        )
        layer.add_to(m)

    choropleth("idaci_score", idaci_cmap, "IDACI (child income deprivation)", show=True)
    choropleth("readiness_score", readiness_cmap, "Infrastructure readiness (0-1)", show=False)
    m.add_child(idaci_cmap)
    m.add_child(readiness_cmap)

    for category, label in CATEGORY_LABELS.items():
        group = folium.FeatureGroup(name=f"Facilities: {label}", show=False)
        for _, row in facilities[facilities["category"] == category].iterrows():
            folium.CircleMarker(
                location=[row.geometry.y, row.geometry.x],
                radius=3,
                color=CATEGORY_COLOURS[category],
                fill=True,
                fill_opacity=0.8,
                weight=1,
                tooltip=(row.get("name") or label.rstrip("s")) + (
                    f" ({row['sport']})" if row.get("sport") else ""
                ),
            ).add_to(group)
        group.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    title = (
        '<div style="position:fixed;top:10px;left:50px;z-index:9999;'
        'background:rgba(255,255,255,0.9);padding:6px 12px;border-radius:4px;'
        'font-family:sans-serif;font-size:14px;box-shadow:0 1px 4px rgba(0,0,0,0.3);">'
        "<b>Leicester: child income deprivation (IDACI) vs football infrastructure readiness</b><br>"
        '<span style="font-size:11px;">LSOA level &middot; IoD 2019 &middot; '
        "facilities &copy; OpenStreetMap contributors (ODbL)</span></div>"
    )
    m.get_root().html.add_child(folium.Element(title))
    m.save(out_html)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", default=Path("data"), type=Path,
                        help="Cache directory for downloaded source data")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    parser.add_argument("--idaci", type=Path,
                        help="Local IDACI CSV/Excel (skips gov.uk download)")
    parser.add_argument("--boundaries", type=Path,
                        help="Local LSOA boundaries GeoJSON/Shapefile (skips ONS download)")
    parser.add_argument("--facilities", type=Path,
                        help="Local facilities GeoJSON with a 'category' property "
                             "(skips Overpass download)")
    parser.add_argument("--pitch-buffer", type=float, default=800,
                        help="Catchment around each LSOA for pitches, metres")
    parser.add_argument("--centre-buffer", type=float, default=1200,
                        help="Catchment for sports centres/halls, metres")
    parser.add_argument("--park-buffer", type=float, default=800,
                        help="Catchment for parks/recreation grounds, metres")
    parser.add_argument("--pitch-weight", type=float, default=0.5)
    parser.add_argument("--centre-weight", type=float, default=0.3)
    parser.add_argument("--park-weight", type=float, default=0.2)
    args = parser.parse_args()

    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.outdir.mkdir(parents=True, exist_ok=True)

    idaci = load_idaci(args.idaci) if args.idaci else fetch_idaci(args.data_dir)
    boundaries = (
        load_boundaries(args.boundaries) if args.boundaries else fetch_boundaries(args.data_dir)
    )
    facilities = (
        gpd.read_file(args.facilities) if args.facilities
        else fetch_facilities(args.data_dir, boundaries)
    )
    if facilities.crs is None:
        facilities = facilities.set_crs(epsg=4326)

    merged = boundaries.merge(idaci, on="lsoa_code", how="left").to_crs(epsg=4326)
    missing = merged["idaci_score"].isna().sum()
    if missing:
        print(f"Warning: {missing} of {len(merged)} LSOAs have no IDACI match.", file=sys.stderr)

    buffers = {
        "pitch": args.pitch_buffer,
        "sports_centre": args.centre_buffer,
        "park": args.park_buffer,
    }
    weights = {
        "pitch": args.pitch_weight,
        "sports_centre": args.centre_weight,
        "park": args.park_weight,
    }
    merged = compute_readiness(merged, facilities, buffers, weights)

    csv_cols = [c for c in merged.columns if c != "geometry"]
    out_csv = args.outdir / "leicester_readiness_lsoa.csv"
    merged[csv_cols].to_csv(out_csv, index=False)

    out_html = args.outdir / "leicester_idaci_readiness_map.html"
    build_map(merged, facilities.to_crs(epsg=4326), out_html)

    print(f"Wrote {out_html}")
    print(f"Wrote {out_csv}")
    print(
        f"LSOAs: {len(merged)} | readiness min/median/max: "
        f"{merged['readiness_score'].min():.2f}/"
        f"{merged['readiness_score'].median():.2f}/"
        f"{merged['readiness_score'].max():.2f}"
    )


if __name__ == "__main__":
    main()
