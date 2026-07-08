"""Stage 5 — render the interactive Leaflet map (Folium) with one toggleable
layer per metric, a dissolved ward layer, and asset point overlays.

Output: <output_dir>/city_plan.html
"""
from __future__ import annotations

import branca.colormap as cm
import folium
import geopandas as gpd

from common import find_column, load_config, read_table, resolve

# Continuous metrics -> (display name, colormap palette, default-visible?)
CHOROPLETHS = [
    ("priority_score", "Priority score", "YlOrRd_09", True),
    ("idaci_score", "IDACI score", "OrRd_09", False),
    ("imd_score", "IMD score", "PuBu_09", False),
    ("youth_population", "Youth population (0-15)", "BuPu_09", False),
    ("need_score", "Need score", "Reds_09", False),
    ("infrastructure_readiness", "Infrastructure readiness", "YlGn_09", False),
]

QUADRANT_COLORS = {
    "Cold spot (priority)": "#b10026",
    "High need, well served": "#fd8d3c",
    "Over-provided": "#41b6c4",
    "Low demand": "#c7e9b4",
}
COVERAGE_COLORS = {
    "None/Very low": "#d73027", "Low": "#fc8d59", "Moderate": "#fee08b",
    "Good": "#91cf60", "High": "#1a9850",
}
ASSET_STYLE = {
    "schools": ("#1f78b4", "graduation-cap"),
    "football_pitches": ("#33a02c", "futbol"),
    "football_providers": ("#6a3d9a", "users"),
    "youth_mobility_centres": ("#e31a1c", "building"),
}

TOOLTIP_FIELDS = [
    ("lsoa_code", "LSOA"), ("ward_name", "Ward"),
    ("idaci_score", "IDACI"), ("imd_score", "IMD"),
    ("youth_population", "Youth 0-15"), ("assets_total", "Assets"),
    ("provision_per_1000_youth", "Provision / 1k youth"),
    ("coverage_level", "Coverage"), ("cold_spot_quadrant", "Quadrant"),
    ("priority_score", "Priority"),
]


def add_choropleth(m, gdf, field, name, palette, show, add_legend):
    vals = gdf[field].dropna()
    if vals.empty:
        return
    colormap = getattr(cm.linear, palette).scale(vals.min(), vals.max())
    colormap.caption = name
    fields = [f for f, _ in TOOLTIP_FIELDS if f in gdf.columns]
    aliases = [a for f, a in TOOLTIP_FIELDS if f in gdf.columns]
    fg = folium.FeatureGroup(name=name, show=show)
    folium.GeoJson(
        gdf,
        style_function=lambda feat, f=field, c=colormap: {
            "fillColor": c(feat["properties"][f]) if feat["properties"].get(f) is not None else "#cccccc",
            "color": "#555555", "weight": 0.4, "fillOpacity": 0.75,
        },
        highlight_function=lambda feat: {"weight": 2, "color": "#000000"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, localize=True),
    ).add_to(fg)
    fg.add_to(m)
    if add_legend:
        colormap.add_to(m)


def add_categorical(m, gdf, field, name, colors, show):
    fields = [f for f, _ in TOOLTIP_FIELDS if f in gdf.columns]
    aliases = [a for f, a in TOOLTIP_FIELDS if f in gdf.columns]
    fg = folium.FeatureGroup(name=name, show=show)
    folium.GeoJson(
        gdf,
        style_function=lambda feat, f=field: {
            "fillColor": colors.get(feat["properties"].get(f), "#cccccc"),
            "color": "#555555", "weight": 0.4, "fillOpacity": 0.75,
        },
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, localize=True),
    ).add_to(fg)
    fg.add_to(m)


def add_ward_layer(m, wards):
    if "ward_provision_score" not in wards.columns:
        return
    vals = wards["ward_provision_score"].dropna()
    colormap = cm.linear.Greens_09.scale(vals.min(), vals.max()) if not vals.empty else None
    fg = folium.FeatureGroup(name="Ward provision score", show=False)
    fields = [f for f in ("ward_name", "youth_population", "assets_total",
                          "ward_provision_score", "priority_score", "cold_spots")
              if f in wards.columns]
    folium.GeoJson(
        wards,
        style_function=lambda feat: {
            "fillColor": colormap(feat["properties"]["ward_provision_score"])
            if colormap and feat["properties"].get("ward_provision_score") is not None else "#cccccc",
            "color": "#222222", "weight": 1.2, "fillOpacity": 0.6,
        },
        tooltip=folium.GeoJsonTooltip(fields=fields),
    ).add_to(fg)
    fg.add_to(m)

    # Ward outlines (always-on reference).
    outline = folium.FeatureGroup(name="Ward boundaries", show=True)
    folium.GeoJson(wards, style_function=lambda f: {
        "fillOpacity": 0, "color": "#111111", "weight": 1.5}).add_to(outline)
    outline.add_to(m)


def add_assets(m, cfg):
    for asset_type, path in cfg["paths"]["assets"].items():
        try:
            df = read_table(resolve(path))
        except Exception:
            continue
        lon = find_column(df.columns, ["lon"]) or find_column(df.columns, ["lng"]) or find_column(df.columns, ["x"])
        lat = find_column(df.columns, ["lat"]) or find_column(df.columns, ["y"])
        if lon is None or lat is None:
            continue
        color, icon = ASSET_STYLE.get(asset_type, ("#888888", "circle"))
        fg = folium.FeatureGroup(name=f"⚑ {asset_type.replace('_', ' ').title()}", show=False)
        name_col = find_column(df.columns, ["name"])
        for _, row in df.iterrows():
            folium.CircleMarker(
                location=[row[lat], row[lon]], radius=4, color=color,
                fill=True, fill_opacity=0.9,
                tooltip=str(row[name_col]) if name_col else asset_type,
            ).add_to(fg)
        fg.add_to(m)


def main():
    cfg = load_config()
    out = resolve(cfg["output_dir"])
    lsoa = gpd.read_file(out / "city_plan_lsoa.geojson")
    wards = gpd.read_file(out / "city_plan_wards.geojson")

    minx, miny, maxx, maxy = lsoa.total_bounds
    centre = [(miny + maxy) / 2, (minx + maxx) / 2]
    m = folium.Map(location=centre, zoom_start=12, tiles="cartodbpositron",
                   control_scale=True)

    for i, (field, name, palette, show) in enumerate(CHOROPLETHS):
        if field in lsoa.columns:
            add_choropleth(m, lsoa, field, name, palette, show, add_legend=(i == 0))
    add_categorical(m, lsoa, "cold_spot_quadrant", "Cold-spot quadrants", QUADRANT_COLORS, False)
    add_categorical(m, lsoa, "coverage_level", "Coverage level", COVERAGE_COLORS, False)
    add_ward_layer(m, wards)
    add_assets(m, cfg)

    folium.LayerControl(collapsed=False).add_to(m)
    title = (f'<div style="position:fixed;top:10px;left:50px;z-index:9999;'
             f'background:white;padding:6px 12px;border-radius:4px;'
             f'box-shadow:0 1px 4px rgba(0,0,0,.3);font-family:sans-serif;">'
             f'<b>{cfg["city"]["name"]} — Interactive City Plan</b></div>')
    m.get_root().html.add_child(folium.Element(title))

    path = out / "city_plan.html"
    m.save(str(path))
    print(f"Wrote {path}.")


if __name__ == "__main__":
    main()
