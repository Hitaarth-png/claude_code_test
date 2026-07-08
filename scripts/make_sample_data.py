"""Generate SYNTHETIC stand-in data so the pipeline runs end-to-end without the
real gov.uk/ONS downloads.

Everything produced here is fabricated (a grid of pseudo-LSOAs over roughly the
Wolverhampton area). It is for demonstrating and verifying the pipeline only —
NOT for analysis. Replace with the real files listed in README.md for real work.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, box

from common import load_config, resolve

# Rough British National Grid (EPSG:27700) bounding box for Wolverhampton.
EAST_MIN, EAST_MAX = 388000, 397000
NORTH_MIN, NORTH_MAX = 295000, 302000
N_COLS, N_ROWS = 12, 12          # 144 pseudo-LSOAs
WARD_COLS, WARD_ROWS = 5, 4      # -> 20 pseudo-wards

WARD_NAMES = [
    "Bilston East", "Bilston North", "Blakenhall", "Bushbury North",
    "Bushbury South & Low Hill", "East Park", "Ettingshall", "Fallings Park",
    "Graiseley", "Heath Town", "Merry Hill", "Oxley", "Park", "Penn",
    "Spring Vale", "St Peter's", "Tettenhall Regis", "Tettenhall Wightwick",
    "Wednesfield North", "Wednesfield South",
]


def build_lsoas(rng: random.Random):
    dx = (EAST_MAX - EAST_MIN) / N_COLS
    dy = (NORTH_MAX - NORTH_MIN) / N_ROWS
    cx, cy = (EAST_MIN + EAST_MAX) / 2, (NORTH_MIN + NORTH_MAX) / 2
    max_r = math.hypot(EAST_MAX - cx, NORTH_MAX - cy)

    rows = []
    for r in range(N_ROWS):
        for c in range(N_COLS):
            e0 = EAST_MIN + c * dx
            n0 = NORTH_MIN + r * dy
            geom = box(e0, n0, e0 + dx, n0 + dy)
            code = f"E01{35000 + r * N_COLS + c:06d}"
            row_grp = min(r * WARD_ROWS // N_ROWS, WARD_ROWS - 1)
            col_grp = min(c * WARD_COLS // N_COLS, WARD_COLS - 1)
            ward = WARD_NAMES[row_grp * WARD_COLS + col_grp]
            # Deprivation rises toward the centre; add noise.
            centre_e, centre_n = e0 + dx / 2, n0 + dy / 2
            proximity = 1 - math.hypot(centre_e - cx, centre_n - cy) / max_r
            depriv = max(0.02, min(0.6, 0.15 + 0.35 * proximity + rng.uniform(-0.08, 0.08)))
            imd_score = max(1, min(80, 10 + 60 * proximity + rng.uniform(-8, 8)))
            youth = int(150 + 500 * proximity + rng.uniform(-80, 120))
            rows.append({
                "lsoa_code": code,
                "lsoa_name": f"Wolverhampton {code[-3:]}",
                "ward_name": ward,
                "geometry": geom,
                "_idaci": round(depriv, 4),
                "_imd": round(imd_score, 2),
                "_youth": max(30, youth),
            })
    return gpd.GeoDataFrame(rows, crs="EPSG:27700")


def rank_to_decile(series: pd.Series, ascending: bool) -> pd.Series:
    # 1 = most deprived (English IoD convention).
    order = series.rank(ascending=ascending, method="first")
    return (pd.qcut(order, 10, labels=False, duplicates="drop") + 1).astype(int)


def scatter(rng, n):
    return [Point(rng.uniform(EAST_MIN, EAST_MAX), rng.uniform(NORTH_MIN, NORTH_MAX))
            for _ in range(n)]


def write_points(gdf_wgs, points, cols, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    g = gpd.GeoDataFrame(cols, geometry=points, crs="EPSG:27700").to_crs(4326)
    df = pd.DataFrame(cols)
    df["lon"] = g.geometry.x
    df["lat"] = g.geometry.y
    df.to_csv(path, index=False)


def main():
    cfg = load_config()
    rng = random.Random(20260708)  # deterministic

    lsoas = build_lsoas(rng)

    # Boundaries (WGS84 GeoJSON, like the real ONS export).
    bnd = lsoas[["lsoa_code", "lsoa_name", "geometry"]].to_crs(4326)
    bpath = resolve(cfg["paths"]["boundaries"])
    bpath.parent.mkdir(parents=True, exist_ok=True)
    bnd.to_file(bpath, driver="GeoJSON")

    # LSOA -> Ward lookup.
    lsoas[["lsoa_code", "lsoa_name", "ward_name"]].to_csv(
        resolve(cfg["paths"]["lsoa_ward_lookup"]), index=False)

    # IMD / IDACI table.
    imd = lsoas[["lsoa_code", "lsoa_name"]].copy()
    imd["IDACI Score"] = lsoas["_idaci"].values
    imd["IMD Score"] = lsoas["_imd"].values
    imd["IDACI Decile"] = rank_to_decile(lsoas["_idaci"], ascending=True).values
    imd["IMD Decile"] = rank_to_decile(lsoas["_imd"], ascending=True).values
    imd.to_csv(resolve(cfg["paths"]["imd"]), index=False)

    # Youth population 0-15.
    yp = lsoas[["lsoa_code"]].copy()
    yp["youth_population_0_15"] = lsoas["_youth"].values
    yp.to_csv(resolve(cfg["paths"]["youth_population"]), index=False)

    # Asset point files, denser where deprivation is lower (provision gaps).
    assets = cfg["paths"]["assets"]
    for key, n, name_prefix in [
        ("schools", 78, "School"),
        ("football_pitches", 46, "Pitch"),
        ("football_providers", 28, "Provider"),
        ("youth_mobility_centres", 12, "Youth Centre"),
    ]:
        pts = scatter(rng, n)
        cols = [{"name": f"{name_prefix} {i+1}"} for i in range(n)]
        write_points(lsoas, pts, cols, resolve(assets[key]))

    print(f"Wrote synthetic data for {cfg['city']['name']} "
          f"({len(lsoas)} pseudo-LSOAs, {len(WARD_NAMES)} pseudo-wards).")
    print("NOTE: fabricated data for pipeline demonstration only.")


if __name__ == "__main__":
    main()
