"""Generate demonstration data on top of the REAL city boundary geometry.

Geometry is genuine ONS LSOA / ward boundary data for the configured authority
(fetched from a public mirror, so the map has the true city shape). Only the
attribute values (IDACI, IMD, youth population) and asset points are SYNTHETIC —
fabricated for pipeline demonstration, NOT for analysis. Replace the attribute
and asset files with the real gov.uk/ONS/FA sources for real work.
"""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from common import load_config, resolve

RAW_DIR = Path("data/raw")


def fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 100:
        return dest
    print(f"Downloading {url}")
    subprocess.run(["curl", "-sSf", "--max-time", "60", "-o", str(dest), url], check=True)
    return dest


def load_real_boundaries(cfg):
    lad = cfg["city"]["lad_code"]
    src = cfg["boundary_source"]
    ward_raw = fetch(src["wards"].format(lad=lad), resolve(str(RAW_DIR / f"wards_{lad}.json")))

    # Prefer an already-written boundary file (e.g. real 2021 geometry from
    # prepare_imd) so synthetic attributes share the same LSOA set; otherwise
    # fall back to the public mirror.
    bpath = resolve(cfg["paths"]["boundaries"])
    if bpath.exists():
        lsoas = gpd.read_file(bpath).to_crs(4326)
        if "lsoa_code" not in lsoas.columns:
            code = next(c for c in lsoas.columns if "CD" in str(c).upper())
            lsoas = lsoas.rename(columns={code: "lsoa_code"})
        if "lsoa_name" not in lsoas.columns:
            lsoas["lsoa_name"] = lsoas["lsoa_code"]
        lsoas = lsoas[["lsoa_code", "lsoa_name", "geometry"]]
    else:
        lsoa_raw = fetch(src["lsoa"].format(lad=lad), resolve(str(RAW_DIR / f"lsoa_{lad}.json")))
        lsoas = gpd.read_file(lsoa_raw)
        code = "LSOA11CD" if "LSOA11CD" in lsoas else next(c for c in lsoas.columns if "CD" in str(c).upper())
        name = "LSOA11NM" if "LSOA11NM" in lsoas else code
        lsoas = lsoas.rename(columns={code: "lsoa_code", name: "lsoa_name"})[
            ["lsoa_code", "lsoa_name", "geometry"]].set_crs(4326, allow_override=True)

    wards = gpd.read_file(ward_raw)
    wname = next((c for c in wards.columns if str(c).upper().endswith("NM") and "NMW" not in str(c).upper()),
                 wards.columns[1])
    wards = wards.rename(columns={wname: "ward_name"})[["ward_name", "geometry"]].set_crs(
        4326, allow_override=True)
    return lsoas, wards


def assign_wards(lsoas, wards, work_crs):
    """Spatially assign each LSOA to its ward via the LSOA's representative point."""
    l = lsoas.to_crs(work_crs).copy()
    w = wards.to_crs(work_crs)
    pts = l.copy()
    pts["geometry"] = l.geometry.representative_point()
    joined = gpd.sjoin(pts, w[["ward_name", "geometry"]], how="left", predicate="within")
    joined = joined[~joined.index.duplicated(keep="first")]
    l["ward_name"] = joined["ward_name"].fillna("Unassigned").values
    return l


def sample_points(polygon, n, rng):
    minx, miny, maxx, maxy = polygon.bounds
    out = []
    while len(out) < n:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if polygon.contains(p):
            out.append(p)
    return out


def rank_to_decile(series, ascending):
    order = series.rank(ascending=ascending, method="first")
    return (pd.qcut(order, 10, labels=False, duplicates="drop") + 1).astype(int)


def main():
    cfg = load_config()
    work_crs = cfg["crs"]["working"]
    rng = random.Random(20260708)

    def need(cfg_path):
        """True if this input is missing and must be fabricated (real files win)."""
        return not resolve(cfg_path).exists()

    lsoas, wards = load_real_boundaries(cfg)
    l = assign_wards(lsoas, wards, work_crs)  # projected, with ward_name

    # Boundaries output (WGS84), like a real ONS export.
    if need(cfg["paths"]["boundaries"]):
        bpath = resolve(cfg["paths"]["boundaries"])
        bpath.parent.mkdir(parents=True, exist_ok=True)
        l.to_crs(4326)[["lsoa_code", "lsoa_name", "geometry"]].to_file(bpath, driver="GeoJSON")

    # LSOA -> ward lookup.
    if need(cfg["paths"]["lsoa_ward_lookup"]):
        l[["lsoa_code", "lsoa_name", "ward_name"]].to_csv(
            resolve(cfg["paths"]["lsoa_ward_lookup"]), index=False)

    # Synthetic attributes with a real spatial gradient (deprivation rises toward
    # the geographic centre; noise added).
    cent = l.geometry.representative_point()
    cx, cy = cent.x.mean(), cent.y.mean()
    dist = ((cent.x - cx) ** 2 + (cent.y - cy) ** 2) ** 0.5
    prox = 1 - (dist / dist.max())
    l = l.reset_index(drop=True)
    idaci = (0.15 + 0.35 * prox + pd.Series([rng.uniform(-0.08, 0.08) for _ in range(len(l))])).clip(0.02, 0.6)
    imd = (10 + 60 * prox + pd.Series([rng.uniform(-8, 8) for _ in range(len(l))])).clip(1, 85)
    youth = (150 + 500 * prox + pd.Series([rng.uniform(-80, 120) for _ in range(len(l))])).clip(lower=40).astype(int)

    imd_df = pd.DataFrame({
        "lsoa_code": l["lsoa_code"], "lsoa_name": l["lsoa_name"],
        "IDACI Score": idaci.round(4), "IMD Score": imd.round(2),
        "IDACI Decile": rank_to_decile(idaci, ascending=True),
        "IMD Decile": rank_to_decile(imd, ascending=True),
    })
    if need(cfg["paths"]["imd"]):
        imd_df.to_csv(resolve(cfg["paths"]["imd"]), index=False)
    if need(cfg["paths"]["youth_population"]):
        pd.DataFrame({"lsoa_code": l["lsoa_code"], "youth_population_0_15": youth}).to_csv(
            resolve(cfg["paths"]["youth_population"]), index=False)

    # Synthetic asset points scattered within the REAL city polygon — only for
    # asset types with no real file already in place.
    city_poly = l.geometry.union_all() if hasattr(l.geometry, "union_all") else l.geometry.unary_union
    assets = cfg["paths"]["assets"]
    fabricated = []
    for key, n, prefix in [
        ("schools", 80, "School"), ("football_pitches", 48, "Pitch"),
        ("football_providers", 30, "Provider"), ("youth_mobility_centres", 12, "Youth Centre"),
    ]:
        if not need(assets[key]):
            continue
        pts = sample_points(city_poly, n, rng)
        g = gpd.GeoSeries(pts, crs=work_crs).to_crs(4326)
        df = pd.DataFrame({"name": [f"{prefix} {i+1}" for i in range(n)],
                           "lon": g.x.values, "lat": g.y.values})
        path = resolve(assets[key])
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        fabricated.append(key)

    print(f"Real geometry for {cfg['city']['name']}: {len(l)} LSOAs, "
          f"{l['ward_name'].nunique()} wards.")
    print(f"Fabricated synthetic asset layers (no real source): {fabricated or 'none'}")
    print("NOTE: geometry is real; fabricated attributes/assets are for demo only.")


if __name__ == "__main__":
    main()
