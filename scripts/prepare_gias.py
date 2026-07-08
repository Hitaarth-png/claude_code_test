"""Convert a raw GIAS (Get Information About Schools) export into clean asset
CSVs for the pipeline.

Keeps only open establishments in the configured authority, splits children's
centres from schools, reprojects Easting/Northing (EPSG:27700) to lon/lat
(EPSG:4326), and writes the minimal `name, lon, lat, lsoa_code` schema the rest
of the pipeline expects. Only establishment name + location + type are used.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from common import clean_codes, load_config, read_table, resolve

CHILDRENS_CENTRE_GROUP = "Children's Centres"


def to_lonlat(df, working, web):
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df["Easting"], df["Northing"]),
                         crs=working).to_crs(web)
    out = pd.DataFrame({
        "name": df["EstablishmentName"].astype(str).str.strip(),
        "lon": g.geometry.x.values,
        "lat": g.geometry.y.values,
        "lsoa_code": clean_codes(df["LSOA (code)"]),
    })
    return out


def main():
    cfg = load_config()
    raw_path = resolve(cfg["paths"]["gias_raw"])
    if not raw_path.exists():
        print(f"No GIAS file at {raw_path}; skipping.")
        return

    df = read_table(raw_path)
    la_code = cfg["city"]["gias_la_code"]
    df = df[(df["EstablishmentStatus (name)"] == "Open") & (df["LA (code)"] == la_code)]
    df = df.dropna(subset=["Easting", "Northing", "EstablishmentName"])
    if df.empty:
        print(f"No GIAS rows for {cfg['city']['name']} (LA {la_code}); "
              f"leaving school/centre layers to the synthetic step.")
        return

    is_cc = df["EstablishmentTypeGroup (name)"] == CHILDRENS_CENTRE_GROUP
    schools = to_lonlat(df[~is_cc], cfg["crs"]["working"], cfg["crs"]["web"])
    centres = to_lonlat(df[is_cc], cfg["crs"]["working"], cfg["crs"]["web"])

    assets = cfg["paths"]["assets"]
    for name, out_df, key in [("schools", schools, "schools"),
                              ("youth mobility centres (children's centres)", centres,
                               "youth_mobility_centres")]:
        if out_df.empty:
            continue                          # let the synthetic step fabricate this layer
        path = resolve(assets[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        out_df.to_csv(path, index=False)
        print(f"Wrote {len(out_df)} real {name} -> {path}")


if __name__ == "__main__":
    main()
