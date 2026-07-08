#!/usr/bin/env python3
"""
Render an *interactive* choropleth map of IDACI (Income Deprivation Affecting
Children Index) scores for every LSOA in the City of Nottingham unitary
authority.

This is the interactive counterpart to ``idaci_leicester_map.py``: it follows
the same workflow (ingest IDACI scores -> ingest LSOA boundaries -> filter to
the city -> join -> render), but produces a self-contained Leaflet/Folium HTML
map with per-LSOA hover tooltips instead of a static PNG.

Inputs (see README.md for where to obtain them):
  --idaci   CSV/Excel file with IDACI scores per LSOA (English Indices of
            Deprivation release, gov.uk). Must contain an LSOA code column
            (e.g. "LSOA code (2011)") and an IDACI score column (e.g.
            "Income Deprivation Affecting Children Index (IDACI) Score").
            If the file also carries an IDACI decile/rank column it is picked
            up automatically and shown in the tooltip.
  --boundaries  GeoJSON/Shapefile of LSOA boundaries (ONS Open Geography
            Portal). Must contain an LSOA code property (e.g. "LSOA11CD").

Output (written to --outdir):
  nottingham_idaci_interactive_map.html   the interactive map
  nottingham_idaci_lsoa.csv               the joined attribute table
  nottingham_idaci_lsoa.geojson           the joined geometries (EPSG:27700)
"""
import argparse
import re
import sys
from pathlib import Path

import branca.colormap as cm
import folium
import geopandas as gpd
import numpy as np
import pandas as pd

NOTTINGHAM_LAD_CODE = "E06000018"
NOTTINGHAM_LAD_NAME = "Nottingham"

LSOA_CODE_PATTERN = re.compile(r"^E01\d{6}$")


def find_column(columns, keywords, exclude=()):
    """Return the first column whose name contains all keywords (case-insensitive)."""
    for col in columns:
        name = col.lower()
        if all(k in name for k in keywords) and not any(e in name for e in exclude):
            return col
    return None


def load_idaci(path):
    if path.suffix.lower() in (".xls", ".xlsx"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    code_col = find_column(df.columns, ["lsoa", "code"])
    score_col = find_column(df.columns, ["idaci", "score"])
    name_col = find_column(df.columns, ["lsoa", "name"])
    # Optional: a national IDACI decile (1 = most deprived 10% in England).
    decile_col = find_column(df.columns, ["idaci", "decile"])

    if code_col is None or score_col is None:
        raise SystemExit(
            "Could not auto-detect LSOA code / IDACI score columns in "
            f"{path}. Found columns: {list(df.columns)}"
        )

    keep = [code_col, score_col]
    names = ["lsoa_code", "idaci_score"]
    if name_col:
        keep.append(name_col)
        names.append("lsoa_name")
    if decile_col:
        keep.append(decile_col)
        names.append("idaci_decile")

    out = df[keep].copy()
    out.columns = names
    out["lsoa_code"] = out["lsoa_code"].astype(str).str.strip()
    out["idaci_score"] = pd.to_numeric(out["idaci_score"], errors="coerce")
    return out


def load_boundaries(path, lad_code=NOTTINGHAM_LAD_CODE, lad_name=NOTTINGHAM_LAD_NAME):
    gdf = gpd.read_file(path)

    lsoa_code_col = find_column(gdf.columns, ["lsoa"], exclude=["name", "nm"])
    if lsoa_code_col is None:
        for candidate in ("LSOA11CD", "LSOA21CD", "lsoa11cd", "lsoa21cd"):
            if candidate in gdf.columns:
                lsoa_code_col = candidate
                break
    if lsoa_code_col is None:
        raise SystemExit(f"Could not find an LSOA code column in boundaries. Columns: {list(gdf.columns)}")

    gdf = gdf.rename(columns={lsoa_code_col: "lsoa_code"})
    gdf["lsoa_code"] = gdf["lsoa_code"].astype(str).str.strip()

    lad_col = find_column(gdf.columns, ["lad", "cd"]) or find_column(gdf.columns, ["la", "code"])
    if lad_col and gdf[lad_col].astype(str).str.contains(lad_code).any():
        gdf = gdf[gdf[lad_col].astype(str) == lad_code]
    else:
        lad_name_col = find_column(gdf.columns, ["lad", "nm"]) or find_column(gdf.columns, ["la", "name"])
        if lad_name_col and gdf[lad_name_col].astype(str).str.contains(lad_name, case=False).any():
            gdf = gdf[gdf[lad_name_col].astype(str).str.contains(lad_name, case=False)]
        else:
            gdf = gdf[gdf["lsoa_code"].str.match(LSOA_CODE_PATTERN)]

    if gdf.empty:
        raise SystemExit(
            "No LSOAs matched Nottingham in the boundary file. Pass a file "
            "pre-filtered to Nottingham, or check the LAD code/name columns."
        )
    return gdf


def build_colormap(values):
    """A 7-step sequential OrRd colormap keyed on quantiles of the IDACI score.

    Quantiles (not equal intervals) so that colour contrast tracks the spread of
    the city's own LSOAs, matching the visual intent of the Leicester map.
    """
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        raise SystemExit("No numeric IDACI scores to map after the join.")

    palette = ["#fff7ec", "#fee8c8", "#fdd49e", "#fdbb84", "#fc8d59", "#e34a33", "#b30000"]
    # Unique quantile edges (guards against degenerate/duplicate breakpoints).
    edges = np.unique(np.quantile(finite, np.linspace(0, 1, len(palette) + 1)))
    if edges.size < 3:
        edges = np.linspace(float(finite.min()), float(finite.max()) or 1.0, 3)
    colormap = cm.StepColormap(
        colors=palette[: len(edges) - 1],
        index=list(edges),
        vmin=float(finite.min()),
        vmax=float(finite.max()),
        caption="IDACI score — proportion of children 0-15 in income-deprived families (higher = more deprived)",
    )
    return colormap


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--idaci", required=True, type=Path, help="IDACI scores CSV/Excel")
    parser.add_argument("--boundaries", required=True, type=Path, help="LSOA boundaries GeoJSON/Shapefile")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    idaci = load_idaci(args.idaci)
    boundaries = load_boundaries(args.boundaries)

    merged = boundaries.merge(idaci, on="lsoa_code", how="left")

    missing = int(merged["idaci_score"].isna().sum())
    if missing:
        print(
            f"Warning: {missing} of {len(merged)} Nottingham LSOAs have no IDACI score match.",
            file=sys.stderr,
        )

    # Persist the joined data (British National Grid) alongside the map.
    merged.to_crs(epsg=27700).to_file(args.outdir / "nottingham_idaci_lsoa.geojson", driver="GeoJSON")
    merged.drop(columns="geometry").to_csv(args.outdir / "nottingham_idaci_lsoa.csv", index=False)

    # --- Interactive map (WGS84 for Leaflet) ---------------------------------
    wgs = merged.to_crs(epsg=4326)
    colormap = build_colormap(wgs["idaci_score"].to_numpy())

    centroid = wgs.geometry.union_all().centroid
    fmap = folium.Map(
        location=[centroid.y, centroid.x],
        zoom_start=12,
        tiles="cartodbpositron",
        control_scale=True,
    )

    def style_function(feature):
        score = feature["properties"].get("idaci_score")
        colour = "#d9d9d9" if score is None or (isinstance(score, float) and np.isnan(score)) else colormap(score)
        return {"fillColor": colour, "color": "#666666", "weight": 0.5, "fillOpacity": 0.75}

    def highlight_function(_feature):
        return {"weight": 2.5, "color": "#000000", "fillOpacity": 0.9}

    # Display copies for the tooltip: format the score, render deciles as whole
    # numbers, and show "No data" for unmatched LSOAs. The numeric idaci_score
    # column is left untouched so styling/colour keeps full precision.
    wgs = wgs.copy()
    wgs["idaci_score_disp"] = wgs["idaci_score"].map(
        lambda v: "No data" if pd.isna(v) else f"{v:.3f}"
    )

    tooltip_fields = ["lsoa_code"]
    tooltip_aliases = ["LSOA code:"]
    if "lsoa_name" in wgs.columns:
        tooltip_fields.append("lsoa_name")
        tooltip_aliases.append("LSOA name:")
    tooltip_fields.append("idaci_score_disp")
    tooltip_aliases.append("IDACI score:")
    if "idaci_decile" in wgs.columns:
        wgs["idaci_decile_disp"] = wgs["idaci_decile"].map(
            lambda v: "No data" if pd.isna(v) else f"{int(round(v))}"
        )
        tooltip_fields.append("idaci_decile_disp")
        tooltip_aliases.append("IDACI decile (1 = most deprived 10% in England):")

    folium.GeoJson(
        wgs,
        name="IDACI by LSOA",
        style_function=style_function,
        highlight_function=highlight_function,
        tooltip=folium.GeoJsonTooltip(fields=tooltip_fields, aliases=tooltip_aliases, sticky=True),
    ).add_to(fmap)

    colormap.add_to(fmap)
    title_html = (
        '<div style="position: fixed; top: 10px; left: 50px; z-index: 9999; '
        'background: rgba(255,255,255,0.9); padding: 8px 12px; border-radius: 6px; '
        'font-family: sans-serif; font-size: 15px; max-width: 460px;">'
        "<b>Income Deprivation Affecting Children Index (IDACI) by LSOA</b><br>"
        "City of Nottingham</div>"
    )
    fmap.get_root().html.add_child(folium.Element(title_html))
    folium.LayerControl(collapsed=False).add_to(fmap)

    out_html = args.outdir / "nottingham_idaci_interactive_map.html"
    fmap.save(str(out_html))
    print(f"Wrote {out_html}")
    print(f"Wrote {args.outdir / 'nottingham_idaci_lsoa.csv'}")
    print(f"Wrote {args.outdir / 'nottingham_idaci_lsoa.geojson'}")


if __name__ == "__main__":
    main()
