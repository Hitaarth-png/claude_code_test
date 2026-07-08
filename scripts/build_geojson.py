"""Stage 4 — merge the per-LSOA metrics onto the boundary geometry and build a
dissolved ward layer. Writes web-CRS GeoJSON for the interactive map.

Outputs: <output_dir>/city_plan_lsoa.geojson, <output_dir>/city_plan_wards.geojson
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from common import clean_codes, find_column, load_config, resolve


def main():
    cfg = load_config()
    work = resolve(cfg["work_dir"])
    out = resolve(cfg["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    web_crs = cfg["crs"]["web"]

    boundaries = gpd.read_file(resolve(cfg["paths"]["boundaries"]))
    code_col = find_column(boundaries.columns, ["lsoa"], exclude=["name"]) or "lsoa_code"
    if code_col not in boundaries:
        for c in ("LSOA21CD", "LSOA11CD", "lsoa_code"):
            if c in boundaries.columns:
                code_col = c
                break
    boundaries = boundaries.rename(columns={code_col: "lsoa_code"})
    boundaries["lsoa_code"] = clean_codes(boundaries["lsoa_code"])

    metrics = pd.read_csv(work / "lsoa_metrics.csv")
    metrics["lsoa_code"] = clean_codes(metrics["lsoa_code"])

    merged = boundaries[["lsoa_code", "geometry"]].merge(metrics, on="lsoa_code", how="inner")
    merged = merged.to_crs(web_crs)
    lsoa_path = out / "city_plan_lsoa.geojson"
    merged.to_file(lsoa_path, driver="GeoJSON")

    # Ward layer: dissolve LSOA geometry by ward, attach ward metrics.
    wards_geom = merged[["ward_name", "geometry"]].dissolve(by="ward_name").reset_index()
    ward_metrics = pd.read_csv(work / "ward_metrics.csv")
    wards = wards_geom.merge(ward_metrics, on="ward_name", how="left")
    ward_path = out / "city_plan_wards.geojson"
    wards.to_file(ward_path, driver="GeoJSON")

    print(f"Wrote {lsoa_path} ({len(merged)} LSOAs) and {ward_path} ({len(wards)} wards).")


if __name__ == "__main__":
    main()
