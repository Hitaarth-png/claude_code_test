#!/usr/bin/env python3
"""
Render a choropleth map of IDACI (Income Deprivation Affecting Children Index)
scores for every LSOA in the City of Leicester unitary authority.

Inputs (see README.md for where to obtain them):
  --idaci   CSV/Excel file with IDACI scores per LSOA (English Indices of
            Deprivation 2019 release, gov.uk). Must contain an LSOA code
            column (e.g. "LSOA code (2011)") and an IDACI score column
            (e.g. "Income Deprivation Affecting Children Index (IDACI) Score").
  --boundaries  GeoJSON/Shapefile of LSOA boundaries (ONS Open Geography
            Portal). Must contain an LSOA code property (e.g. "LSOA11CD").

Output:
  A PNG choropleth map and a CSV of the joined data, written to --outdir.
"""
import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

LEICESTER_LAD_CODE = "E06000016"
LEICESTER_LAD_NAME = "Leicester"

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

    if code_col is None or score_col is None:
        raise SystemExit(
            "Could not auto-detect LSOA code / IDACI score columns in "
            f"{path}. Found columns: {list(df.columns)}"
        )

    out = df[[code_col, score_col] + ([name_col] if name_col else [])].copy()
    out.columns = ["lsoa_code", "idaci_score"] + (["lsoa_name"] if name_col else [])
    out["lsoa_code"] = out["lsoa_code"].astype(str).str.strip()
    return out


def load_boundaries(path, lad_code=LEICESTER_LAD_CODE, lad_name=LEICESTER_LAD_NAME):
    gdf = gpd.read_file(path)

    lsoa_code_col = find_column(gdf.columns, ["lsoa"], exclude=["name"]) or find_column(
        gdf.columns, ["lsoa11cd".lower()]
    )
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
            "No LSOAs matched Leicester in the boundary file. Pass a file "
            "pre-filtered to Leicester, or check the LAD code/name columns."
        )
    return gdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idaci", required=True, type=Path, help="IDACI scores CSV/Excel")
    parser.add_argument("--boundaries", required=True, type=Path, help="LSOA boundaries GeoJSON/Shapefile")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    idaci = load_idaci(args.idaci)
    boundaries = load_boundaries(args.boundaries)

    merged = boundaries.merge(idaci, on="lsoa_code", how="left")

    missing = merged["idaci_score"].isna().sum()
    if missing:
        print(f"Warning: {missing} of {len(merged)} Leicester LSOAs have no IDACI score match.", file=sys.stderr)

    merged.to_crs(epsg=27700).to_file(args.outdir / "leicester_idaci_lsoa.geojson", driver="GeoJSON")
    merged.drop(columns="geometry").to_csv(args.outdir / "leicester_idaci_lsoa.csv", index=False)

    fig, ax = plt.subplots(1, 1, figsize=(11, 11))
    merged.to_crs(epsg=27700).plot(
        column="idaci_score",
        cmap="OrRd",
        linewidth=0.3,
        edgecolor="grey",
        legend=True,
        legend_kwds={"label": "IDACI score (proportion of children 0-15 in income-deprived families)", "shrink": 0.6},
        missing_kwds={"color": "lightgrey", "label": "No data"},
        ax=ax,
    )
    ax.set_title("Income Deprivation Affecting Children Index (IDACI) by LSOA\nCity of Leicester", fontsize=14)
    ax.set_axis_off()
    fig.tight_layout()
    out_png = args.outdir / "leicester_idaci_map.png"
    fig.savefig(out_png, dpi=200)
    print(f"Wrote {out_png}")
    print(f"Wrote {args.outdir / 'leicester_idaci_lsoa.csv'}")
    print(f"Wrote {args.outdir / 'leicester_idaci_lsoa.geojson'}")


if __name__ == "__main__":
    main()
