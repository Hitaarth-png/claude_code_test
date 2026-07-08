"""Stage 2 — for every LSOA, count assets it contains AND score how accessible
assets are to it.

Two measures per asset type:
  * `<type>`         raw containment count (assets strictly inside the LSOA)
  * `<type>_access`  provision score used downstream

`<type>_access` depends on config `provision.method`:
  * containment -> equals the containment count
  * catchment   -> distance-decayed count of assets within `radius_m` of the
                   LSOA's representative point, so an asset just over a boundary
                   still contributes (fixes the border/containment blind spot)

Writes <work_dir>/asset_counts_lsoa.csv (both columns per asset type).
"""
from __future__ import annotations

import math

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
    src_crs = 27700 if df[lon].abs().max() > 1000 else 4326
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df[lon], df[lat]), crs=src_crs)
    return gdf.to_crs(crs_working)[["geometry"]]


def decay_weight(dist, radius, mode):
    if mode == "none":
        return 1.0
    r = dist / radius
    if mode == "gaussian":
        return math.exp(-(r ** 2))
    return max(0.0, 1.0 - r)              # linear (default)


def access_scores(points, lsoa_pts, radius, mode):
    """Distance-decayed asset count within `radius` of each LSOA point."""
    buffers = lsoa_pts.copy()
    buffers["geometry"] = lsoa_pts.geometry.buffer(radius)
    buffers["px"], buffers["py"] = lsoa_pts.geometry.x, lsoa_pts.geometry.y
    pts = points.copy()
    pts["ax"], pts["ay"] = pts.geometry.x, pts.geometry.y
    pairs = gpd.sjoin(pts, buffers[["lsoa_code", "px", "py", "geometry"]],
                      how="inner", predicate="within")
    if pairs.empty:
        return pd.Series(dtype=float)
    d = ((pairs["ax"] - pairs["px"]) ** 2 + (pairs["ay"] - pairs["py"]) ** 2) ** 0.5
    pairs["w"] = [decay_weight(x, radius, mode) for x in d]
    return pairs.groupby("lsoa_code")["w"].sum()


def main():
    cfg = load_config()
    work_crs = cfg["crs"]["working"]
    prov = cfg.get("provision", {})
    method = prov.get("method", "containment")
    radius = float(prov.get("radius_m", 1500))
    decay = prov.get("decay", "linear")

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

    lsoa_pts = base.copy()
    lsoa_pts["geometry"] = base.geometry.representative_point()

    counts = base[["lsoa_code"]].copy()
    asset_cols = list(cfg["paths"]["assets"].keys())
    for asset_type in asset_cols:
        pts = load_points(resolve(cfg["paths"]["assets"][asset_type]), work_crs)
        inside = gpd.sjoin(pts, base, how="inner", predicate="within")
        per_lsoa = inside.groupby("lsoa_code").size().rename(asset_type)
        counts = counts.merge(per_lsoa, on="lsoa_code", how="left")

        if method == "catchment":
            acc = access_scores(pts, lsoa_pts, radius, decay).rename(f"{asset_type}_access")
            counts = counts.merge(acc, on="lsoa_code", how="left")
            reach = (counts[f"{asset_type}_access"] > 0).sum()
            print(f"{asset_type}: {int(per_lsoa.sum())} inside; "
                  f"reachable within {int(radius)}m for {reach} LSOAs.")
        else:
            print(f"{asset_type}: {int(per_lsoa.sum())} points matched to LSOAs.")

    counts[asset_cols] = counts[asset_cols].fillna(0).astype(int)
    access_cols = [f"{c}_access" for c in asset_cols]
    if method == "catchment":
        counts[access_cols] = counts[access_cols].fillna(0.0).round(3)
    else:
        for c in asset_cols:                      # containment: access == count
            counts[f"{c}_access"] = counts[c].astype(float)

    out = resolve(cfg["work_dir"]) / "asset_counts_lsoa.csv"
    counts.to_csv(out, index=False)
    print(f"Wrote {out} (provision method: {method}).")


if __name__ == "__main__":
    main()
