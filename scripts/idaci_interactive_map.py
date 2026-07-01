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

        props = {
            "lsoa_code": row.get("LSOA code"),
            "lsoa_name": row.get("LSOA name"),
            "ward": row.get("Ward Name"),
            "msoa_name": row.get("MSOA HCL Name") or row.get("MSOA Name"),
            "parliamentary_constituency": row.get("Parliamentary Constituency"),
            "idaci_score": (
                round(to_float(row.get("IDACI_score")) * 100, 1)
                if to_float(row.get("IDACI_score")) is not None
                else None
            ),
            "idaci_rank": to_int(row.get("IDACI_rank")),
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
    </fieldset>

    <fieldset>
      <legend>Filter by ward</legend>
      <select id="wardFilter"><option value="">All wards</option></select>
    </fieldset>

    <fieldset>
      <legend>Legend</legend>
      <div id="legend"></div>
    </fieldset>

    <div id="summary"></div>

    <footer>
      Source: 2025 English Indices of Deprivation (IDACI sub-domain), ONS mid-2022 population estimates.
      Deciles: 1 = most deprived 10% of LSOAs nationally, 10 = least deprived.
      Click an area for details.
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

function colourForPct(v, min, max) {
  if (v === null || v === undefined) return NO_DATA_COLOUR;
  const t = max > min ? (v - min) / (max - min) : 0;
  // pale yellow (low) -> dark red (high share of children)
  const stops = ["#ffffe5", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#a50f15", "#67000d"];
  const idx = Math.min(stops.length - 1, Math.floor(t * stops.length));
  return stops[idx];
}

let pctRange = { min: 0, max: 100 };
(function computePctRange() {
  const vals = DATA.features.map(f => f.properties.pct_children_0015).filter(v => v !== null && v !== undefined);
  if (vals.length) {
    pctRange.min = Math.min(...vals);
    pctRange.max = Math.max(...vals);
  }
})();

function styleFor(feature) {
  const p = feature.properties;
  let fill;
  if (currentMetric === 'pct_children_0015') {
    fill = colourForPct(p.pct_children_0015, pctRange.min, pctRange.max);
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
      <tr><td class="k">IDACI rank (nationally)</td><td>${fmt(p.idaci_rank)}</td></tr>
      <tr><td class="k">Overall IMD decile</td><td>${fmt(p.imd_decile)}</td></tr>
      <tr><td class="k">Population 0&ndash;15</td><td>${fmt(p.pop_0015)} (${fmt(p.pct_children_0015, '%')} of area)</td></tr>
      <tr><td class="k">Total population</td><td>${fmt(p.pop_total)}</td></tr>
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
  if (currentWard) {
    const bounds = L.geoJSON(DATA.features.filter(passesFilter)).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] });
  } else {
    map.fitBounds(L.geoJSON(DATA).getBounds());
  }
});

renderLegend();
rebuildLayer();
map.fitBounds(L.geoJSON(DATA).getBounds());
</script>
</body>
</html>
"""


def build_html(features):
    geojson = {"type": "FeatureCollection", "features": features}
    leaflet_js = (VENDOR_DIR / "leaflet.js").read_text(encoding="utf-8")
    leaflet_css = (VENDOR_DIR / "leaflet.css").read_text(encoding="utf-8")
    html = HTML_TEMPLATE.replace("__GEOJSON__", json.dumps(geojson))
    html = html.replace("__DECILE_COLOURS__", json.dumps(DECILE_COLOURS))
    html = html.replace("__NO_DATA_COLOUR__", NO_DATA_COLOUR)
    html = html.replace("__LEAFLET_JS__", leaflet_js)
    html = html.replace("__LEAFLET_CSS__", leaflet_css)
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Deprivation-in-Leicester CSV")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    rows = list(load_rows(args.input))
    features = build_features(rows)
    if not features:
        raise SystemExit("No features parsed from input CSV - check the file format.")

    html = build_html(features)
    out_html = args.outdir / "leicester_idaci_interactive_map.html"
    out_html.write_text(html, encoding="utf-8")

    out_geojson = args.outdir / "leicester_idaci_lsoa.geojson"
    out_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, indent=None),
        encoding="utf-8",
    )

    print(f"Parsed {len(features)} LSOAs")
    print(f"Wrote {out_html}")
    print(f"Wrote {out_geojson}")


if __name__ == "__main__":
    main()
