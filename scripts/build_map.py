"""Stage 5 — render the interactive Leaflet map (Folium).

Informative & interactive build:
  * grouped layer control (choropleths as one exclusive radio group; ward +
    asset overlays as a multi-select group)
  * per-layer legends that swap as the active layer changes
  * hover tooltip (concise) + click popup (full stats incl. asset breakdown)
  * summary + top-10 priority side panel with fly-to
  * colourblind-safe (viridis-family) continuous palettes
  * Fullscreen / MiniMap / MousePosition controls + ward search
  * clustered asset markers

Output: <output_dir>/city_plan.html
"""
from __future__ import annotations

import json

import branca.colormap as cm
import folium
import geopandas as gpd
from folium.plugins import Fullscreen, GroupedLayerControl, MarkerCluster, MiniMap, MousePosition, Search

from common import find_column, load_config, read_table, resolve

# (field, display name, viridis-family palette, default-visible?)
CHOROPLETHS = [
    ("priority_score", "Priority score", "magma", True),
    ("idaci_score", "IDACI score", "inferno", False),
    ("imd_score", "IMD score", "viridis", False),
    ("youth_population", "Youth population (0-15)", "viridis", False),
    ("need_score", "Need score", "plasma", False),
    ("infrastructure_readiness", "Infrastructure readiness", "viridis", False),
]
QUADRANT_COLORS = {
    "Cold spot (priority)": "#7a0177", "High need, well served": "#c51b8a",
    "Over-provided": "#41b6c4", "Low demand": "#c7e9b4",
}
COVERAGE_COLORS = {
    "None/Very low": "#440154", "Low": "#3b528b", "Moderate": "#21918c",
    "Good": "#5ec962", "High": "#fde725",
}
ASSET_STYLE = {
    "schools": ("#1f78b4", "Schools"), "football_pitches": ("#33a02c", "Football pitches"),
    "football_providers": ("#6a3d9a", "Football providers"),
    "youth_mobility_centres": ("#e31a1c", "Youth mobility centres"),
}
ASSET_KEYS = list(ASSET_STYLE.keys())

TOOLTIP = [("lsoa_name", "LSOA"), ("ward_name", "Ward"), ("priority_score", "Priority")]
POPUP = [
    ("lsoa_code", "LSOA code"), ("lsoa_name", "LSOA"), ("ward_name", "Ward"),
    ("idaci_score", "IDACI score"), ("imd_score", "IMD score"),
    ("youth_population", "Youth 0-15"), ("assets_total", "Assets (in LSOA)"),
    ("asset_summary", "Asset breakdown"), ("provision_per_1000_youth", "Provision / 1k youth"),
    ("infrastructure_readiness", "Infrastructure readiness"),
    ("coverage_level", "Coverage level"), ("cold_spot_quadrant", "Quadrant"),
    ("priority_score", "Priority score"),
]


def continuous_legend(field, name, colormap):
    """Small HTML colourbar legend for a continuous layer."""
    stops = 5
    lo, hi = colormap.vmin, colormap.vmax
    cells = "".join(
        f'<span style="flex:1;background:{colormap(lo + (hi-lo)*i/(stops-1))};">&nbsp;</span>'
        for i in range(stops))
    return (f'<div class="cp-legend" data-layer="{field}" style="display:none;">'
            f'<b>{name}</b><div style="display:flex;height:12px;margin:4px 0;">{cells}</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:11px;">'
            f'<span>{lo:.0f}</span><span>{hi:.0f}</span></div></div>')


def categorical_legend(field, name, colors):
    rows = "".join(
        f'<div><span style="display:inline-block;width:12px;height:12px;'
        f'background:{c};margin-right:6px;"></span>{lbl}</div>'
        for lbl, c in colors.items())
    return (f'<div class="cp-legend" data-layer="{field}" style="display:none;">'
            f'<b>{name}</b>{rows}</div>')


def add_choropleth(m, gdf, field, name, palette, show, legends, bindings):
    vals = gdf[field].dropna()
    if vals.empty:
        return
    colormap = getattr(cm.linear, palette).scale(vals.min(), vals.max())
    fields = [f for f, _ in TOOLTIP if f in gdf.columns]
    pfields = [f for f, _ in POPUP if f in gdf.columns]
    paliases = [a for f, a in POPUP if f in gdf.columns]
    fg = folium.FeatureGroup(name=name, show=show, control=False)
    folium.GeoJson(
        gdf,
        style_function=lambda ft, f=field, c=colormap: {
            "fillColor": c(ft["properties"][f]) if ft["properties"].get(f) is not None else "#cccccc",
            "color": "#555", "weight": 0.4, "fillOpacity": 0.78},
        highlight_function=lambda ft: {"weight": 2.5, "color": "#000"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=[a for f, a in TOOLTIP if f in gdf.columns]),
        popup=folium.GeoJsonPopup(fields=pfields, aliases=paliases, max_width=320),
    ).add_to(fg)
    fg.add_to(m)
    legends.append(continuous_legend(field, name, colormap))
    bindings.append((fg.get_name(), field, show))
    return fg


def add_categorical(m, gdf, field, name, colors, legends, bindings):
    fields = [f for f, _ in TOOLTIP if f in gdf.columns]
    pfields = [f for f, _ in POPUP if f in gdf.columns]
    fg = folium.FeatureGroup(name=name, show=False, control=False)
    folium.GeoJson(
        gdf,
        style_function=lambda ft, f=field: {
            "fillColor": colors.get(ft["properties"].get(f), "#cccccc"),
            "color": "#555", "weight": 0.4, "fillOpacity": 0.78},
        highlight_function=lambda ft: {"weight": 2.5, "color": "#000"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=[a for f, a in TOOLTIP if f in gdf.columns]),
        popup=folium.GeoJsonPopup(fields=pfields, aliases=[a for f, a in POPUP if f in gdf.columns], max_width=320),
    ).add_to(fg)
    fg.add_to(m)
    legends.append(categorical_legend(field, name, colors))
    bindings.append((fg.get_name(), field, False))
    return fg


def add_assets(m, cfg):
    groups = []
    for key in ASSET_KEYS:
        path = cfg["paths"]["assets"].get(key)
        try:
            df = read_table(resolve(path))
        except Exception:
            continue
        lon = find_column(df.columns, ["lon"]) or find_column(df.columns, ["lng"]) or find_column(df.columns, ["x"])
        lat = find_column(df.columns, ["lat"]) or find_column(df.columns, ["y"])
        if lon is None or lat is None:
            continue
        color, label = ASSET_STYLE[key]
        singular = label[:-1] if label.endswith("s") else label
        name_col = find_column(df.columns, ["name"])
        fg = folium.FeatureGroup(name=label, show=False, control=False)
        cluster = MarkerCluster().add_to(fg)
        for _, row in df.iterrows():
            nm = str(row[name_col]).strip() if name_col and str(row[name_col]).strip() else singular
            folium.CircleMarker(
                [row[lat], row[lon]], radius=5, color=color, fill=True, fill_opacity=0.9,
                # Permanent label so the name is visible on the pin itself.
                tooltip=folium.Tooltip(nm, permanent=True, direction="top",
                                       className="cp-pin-label"),
                popup=folium.Popup(f'<b>{nm}</b><br><span style="color:{color}">{singular}</span>',
                                   max_width=220),
            ).add_to(cluster)
        fg.add_to(m)
        groups.append(fg)
    return groups


def side_panel(cfg, lsoa, wards):
    totals = {
        "LSOAs": len(lsoa),
        "Youth (0-15)": int(lsoa["youth_population"].sum()),
        "Assets": int(lsoa["assets_total"].sum()),
        "Cold spots": int((lsoa["cold_spot_quadrant"] == "Cold spot (priority)").sum()),
    }
    top_l = lsoa.nlargest(10, "priority_score")
    top_w = wards.nlargest(10, "priority_score")

    def rows(df, label):
        col = label if label in df.columns else "lsoa_code"
        out = []
        for _, r in df.iterrows():
            out.append(f'<li onclick="cpFly({r["_lat"]:.5f},{r["_lng"]:.5f})">'
                       f'<span>{r[col]}</span><b>{r["priority_score"]:.0f}</b></li>')
        return "".join(out)

    stat = "".join(f'<div class="cp-stat"><b>{v:,}</b><span>{k}</span></div>'
                   for k, v in totals.items())
    return (
        f'<div id="cp-panel"><h3>{cfg["city"]["name"]} — City Plan</h3>'
        f'<div class="cp-stats">{stat}</div>'
        f'<div class="cp-tabs"><button class="cp-active" onclick="cpTab(\'lsoa\')">Top LSOAs</button>'
        f'<button onclick="cpTab(\'ward\')">Top wards</button></div>'
        f'<ol id="cp-lsoa" class="cp-rank">{rows(top_l, "lsoa_name")}</ol>'
        f'<ol id="cp-ward" class="cp-rank" style="display:none;">{rows(top_w, "ward_name")}</ol>'
        f'<p class="cp-note">Click a row to zoom. Ranked by priority score.</p></div>')


def main():
    cfg = load_config()
    out = resolve(cfg["output_dir"])
    lsoa = gpd.read_file(out / "city_plan_lsoa.geojson")
    wards = gpd.read_file(out / "city_plan_wards.geojson")

    # Fly-to coordinates (representative point via projected CRS to avoid warnings).
    for gdf in (lsoa, wards):
        pt = gdf.to_crs(cfg["crs"]["working"]).geometry.representative_point().to_crs(cfg["crs"]["web"])
        gdf["_lat"], gdf["_lng"] = pt.y.values, pt.x.values

    # Popup asset-breakdown text.
    def summarise(r):
        parts = [f'{ASSET_STYLE[k][1].split()[0]} {int(r[k])}' for k in ASSET_KEYS if k in lsoa.columns]
        return " · ".join(parts)
    lsoa["asset_summary"] = lsoa.apply(summarise, axis=1)

    minx, miny, maxx, maxy = lsoa.total_bounds
    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], zoom_start=12,
                   tiles="cartodbpositron", control_scale=True)

    legends, bindings = [], []
    metric_groups = []
    for field, name, palette, show in CHOROPLETHS:
        if field in lsoa.columns:
            metric_groups.append(add_choropleth(m, lsoa, field, name, palette, show, legends, bindings))
    metric_groups.append(add_categorical(m, lsoa, "cold_spot_quadrant", "Cold-spot quadrants", QUADRANT_COLORS, legends, bindings))
    metric_groups.append(add_categorical(m, lsoa, "coverage_level", "Coverage level", COVERAGE_COLORS, legends, bindings))

    # Ward provision choropleth (part of the exclusive metric group).
    if "ward_provision_score" in wards.columns:
        metric_groups.append(add_choropleth(m, wards, "ward_provision_score", "Ward provision score",
                                            "viridis", False, legends, bindings))

    # Overlays: ward outline (searchable) + clustered assets.
    outline_fg = folium.FeatureGroup(name="Ward boundaries", show=True, control=False)
    ward_gj = folium.GeoJson(wards, style_function=lambda f: {
        "fillOpacity": 0, "color": "#111", "weight": 1.6},
        tooltip=folium.GeoJsonTooltip(fields=["ward_name"]))
    ward_gj.add_to(outline_fg)
    outline_fg.add_to(m)
    overlay_groups = [outline_fg] + add_assets(m, cfg)

    GroupedLayerControl(groups={"Map layer (choose one)": [g for g in metric_groups if g]},
                        exclusive_groups=True, collapsed=False).add_to(m)
    GroupedLayerControl(groups={"Overlays": overlay_groups},
                        exclusive_groups=False, collapsed=False).add_to(m)

    Search(layer=ward_gj, search_label="ward_name", geom_type="Polygon",
           placeholder="Search a ward", position="topright", collapsed=True).add_to(m)
    Fullscreen().add_to(m)
    MiniMap(toggle_display=True).add_to(m)
    MousePosition(prefix="lat/lon:").add_to(m)

    # Legend container + side panel + interactivity.
    mapv = m.get_name()
    bind_js = "\n".join(
        f'{var}.on("add", function(){{cpLegend("{field}");}});' for var, field, _ in bindings)
    default_field = next((f for _, f, s in bindings if s), bindings[0][1])
    css = """
    <style>
    #cp-panel{position:fixed;top:80px;left:12px;width:240px;max-height:62vh;overflow:auto;
      z-index:9999;background:#fff;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);
      font-family:sans-serif;padding:10px 12px;font-size:13px;}
    #cp-panel h3{margin:0 0 8px;font-size:15px;}
    .cp-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px;}
    .cp-stat{background:#f4f4f6;border-radius:6px;padding:6px;text-align:center;}
    .cp-stat b{display:block;font-size:15px;} .cp-stat span{font-size:11px;color:#555;}
    .cp-tabs button{border:0;background:#eee;padding:4px 8px;cursor:pointer;border-radius:4px;font-size:12px;}
    .cp-tabs button.cp-active{background:#7a0177;color:#fff;}
    .cp-rank{list-style:none;margin:6px 0 0;padding:0;}
    .cp-rank li{display:flex;justify-content:space-between;padding:4px 6px;cursor:pointer;border-radius:4px;}
    .cp-rank li:hover{background:#f0e6f2;} .cp-rank li b{color:#7a0177;}
    .cp-note{font-size:11px;color:#777;margin:8px 0 0;}
    .cp-pin-label{background:rgba(255,255,255,.9);border:0;box-shadow:0 1px 2px rgba(0,0,0,.3);
      font-size:11px;font-family:sans-serif;padding:1px 5px;white-space:nowrap;}
    .cp-pin-label:before{display:none;}
    #cp-legends{position:fixed;bottom:24px;left:12px;z-index:9999;background:#fff;
      border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.3);padding:8px 10px;font-family:sans-serif;
      font-size:12px;min-width:150px;} #cp-legends .cp-legend b{font-size:12px;}
    </style>"""
    js = f"""
    <script>
    function cpLegend(f){{document.querySelectorAll('#cp-legends .cp-legend').forEach(function(e){{
      e.style.display = e.getAttribute('data-layer')===f ? 'block':'none';}});}}
    function cpFly(lat,lng){{{mapv}.flyTo([lat,lng],14);}}
    function cpTab(t){{document.getElementById('cp-lsoa').style.display=t=='lsoa'?'block':'none';
      document.getElementById('cp-ward').style.display=t=='ward'?'block':'none';
      document.querySelectorAll('.cp-tabs button').forEach(function(b){{b.classList.remove('cp-active');}});
      event.target.classList.add('cp-active');}}
    document.addEventListener('DOMContentLoaded', function(){{
      {bind_js}
      cpLegend("{default_field}");
    }});
    </script>"""
    legend_box = '<div id="cp-legends">' + "".join(legends) + "</div>"
    root = m.get_root()
    root.html.add_child(folium.Element(css + side_panel(cfg, lsoa, wards) + legend_box))
    root.html.add_child(folium.Element(js))

    path = out / "city_plan.html"
    m.save(str(path))
    print(f"Wrote {path}.")


if __name__ == "__main__":
    main()
