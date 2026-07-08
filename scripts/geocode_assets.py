"""Stage 2 — assign each asset point to its containing LSOA (spatial join) and
count assets per LSOA and per ward, per asset type.

Writes <work_dir>/asset_counts_lsoa.csv (one column per asset type).
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from common import clean_codes, find_column, load_config, read_table, resolve


def load_points(path, crs_working) -> gpd.GeoDataFrame:
    df = read_table(path)
    lon = find_column(df.columns, ["lon"]) or find_column(df.columns, ["lng"]) \
        or find_column(df.columns, ["easting"]) or find_column(df.columns, ["x"])
    lat = find_column(df.columns, ["lat"]) or find_column(df.columns, ["northing"]) \
        or find_column(df.columns, ["y"])
    if lon is None or lat is None:
        raise SystemExit(f"Asset file {path} needs lon/lat (or easting/northing). Columns: {list(df.columns)}")
    # Heuristic: eastings/northings are large numbers -> already BNG.
    src_crs = 27700 if df[lon].abs().max() > 1000 else 4326
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat]), crs=src_crs)
    return gdf.to_crs(crs_working)


def main():
    cfg = load_config()
    work_crs = cfg["crs"]["working"]

    boundaries = gpd.read_file(resolve(cfg["paths"]["boundaries"])).to_crs(work_crs)
    code_col = find_column(boundaries.columns, ["lsoa"], exclude=["name"]) or "lsoa_code"
    if code_col not in boundaries:
        for c in ("LSOA21CD", "LSOA11CD", "lsoa_code"):
            if c in boundaries.columns:
                code_col = c
                break
    boundaries = boundaries.rename(columns={code_col: "lsoa_code"})
    boundaries["lsoa_code"] = clean_codes(boundaries["lsoa_code"])
    base = boundaries[["lsoa_code", "geometry"]]

    counts = base[["lsoa_code"]].copy()
    for asset_type, path in cfg["paths"]["assets"].items():
        pts = load_points(resolve(path), work_crs)
        # Keep only geometry so any source column (e.g. GIAS lsoa_code) can't
        # collide with the boundary's lsoa_code during the join.
        pts = pts[["geometry"]]
        joined = gpd.sjoin(pts, base, how="inner", predicate="within")
        per_lsoa = joined.groupby("lsoa_code").size().rename(asset_type)
        counts = counts.merge(per_lsoa, on="lsoa_code", how="left")
        print(f"{asset_type}: {int(per_lsoa.sum())} points matched to LSOAs.")

    asset_cols = list(cfg["paths"]["assets"].keys())
    counts[asset_cols] = counts[asset_cols].fillna(0).astype(int)

    out = resolve(cfg["work_dir"]) / "asset_counts_lsoa.csv"
    counts.to_csv(out, index=False)
    print(f"Wrote {out}.")


if __name__ == "__main__":
    main()
