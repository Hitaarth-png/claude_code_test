"""Convert a raw Indices of Deprivation (WMCA / national, LSOA 2021) export into
the pipeline's IMD table AND the LSOA boundary file.

The IoD release ships 2021 LSOA boundaries in a `geom` column, so using it for
both deprivation and geometry keeps the join 1:1 (no 2011<->2021 vintage gap).
Only deprivation deciles/ranks and geometry are used.
"""
from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

from common import clean_codes, find_column, load_config, read_table, resolve


def main():
    cfg = load_config()
    raw = cfg["paths"].get("imd_raw")
    if not raw or not resolve(raw).exists():
        print("No raw IoD file configured/found; skipping.")
        return

    df = read_table(resolve(raw))
    code = find_column(df.columns, ["lsoa", "code"])
    name = find_column(df.columns, ["lsoa", "name"])
    la_code = find_column(df.columns, ["local authority", "code"]) or find_column(df.columns, ["la", "code"])
    df[code] = clean_codes(df[code])
    df = df[df[la_code] == cfg["city"]["lad_code"]]
    if df.empty:
        print(f"No IoD rows for {cfg['city']['name']} ({cfg['city']['lad_code']}) in {raw}; "
              f"boundaries will fall back to the public mirror.")
        return

    imd_dec = find_column(df.columns, ["multiple", "decile"])
    imd_rank = find_column(df.columns, ["multiple", "rank"])
    idaci_dec = find_column(df.columns, ["idaci", "decile"])
    idaci_rank = find_column(df.columns, ["idaci", "rank"])

    imd = pd.DataFrame({"lsoa_code": df[code], "lsoa_name": df[name]})
    for out_col, src in [("imd_decile", imd_dec), ("imd_rank", imd_rank),
                         ("idaci_decile", idaci_dec), ("idaci_rank", idaci_rank)]:
        if src is not None:
            imd[out_col] = pd.to_numeric(df[src], errors="coerce")
    imd_path = resolve(cfg["paths"]["imd"])
    imd_path.parent.mkdir(parents=True, exist_ok=True)
    imd.to_csv(imd_path, index=False)
    print(f"Wrote real IoD deciles/ranks for {len(imd)} LSOAs -> {resolve(cfg['paths']['imd'])}")

    # Boundaries from the embedded geometry (2021).
    geom_col = find_column(df.columns, ["geom"], exclude=["centroid"])
    if geom_col is not None:
        geoms = df[geom_col].apply(lambda g: shape(json.loads(g)))
        gdf = gpd.GeoDataFrame(
            {"lsoa_code": df[code].values, "lsoa_name": df[name].values},
            geometry=geoms.values, crs=cfg["crs"]["web"])
        bpath = resolve(cfg["paths"]["boundaries"])
        bpath.parent.mkdir(parents=True, exist_ok=True)
        gdf.to_file(bpath, driver="GeoJSON")
        print(f"Wrote real 2021 boundaries for {len(gdf)} LSOAs -> {bpath}")


if __name__ == "__main__":
    main()
