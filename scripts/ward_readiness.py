#!/usr/bin/env python3
"""
Compute the ward-level infrastructure readiness & cold-spot quadrant layer
for Leicester, per METHODOLOGY.md.

v1 implementation notes:
  Demand uses the full D1-D3 set (the LSOA extract carries pop_0015 per LSOA).
  Readiness uses S1 (pitches) + S3 (schools) only; S2 (sports halls) and
  S4 (green space) have no data coverage in the repo yet and are dropped from
  the mean (not imputed), with coverage recorded per ward as the methodology
  requires. Facilities within BUFFER_M of another ward's boundary also count
  toward that ward at half weight.

Inputs are the committed geojson outputs - no network access needed.

Outputs (to --outdir):
  leicester_ward_readiness.csv      raw values, z-scores, indices, quadrant
  leicester_ward_readiness.geojson  same, on the dissolved ward polygons
"""
import argparse
import csv
import json
import math
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform

BUFFER_M = 400  # facilities this close to a neighbouring ward count there at half weight

QUADRANTS = {
    "Q1": ("Cold spot - priority", "Highest expansion priority; needs venue investment/partnerships before programmes can scale"),
    "Q2": ("Activate existing assets", "Fastest wins - demand and infrastructure both present"),
    "Q3": ("Comfortable / saturated", "Low priority; existing capacity likely serves current need"),
    "Q4": ("Monitor", "Low current need; revisit as demographics shift"),
}

TO_BNG = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True).transform


def zscores(values):
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
    return [(v - mean) / sd if sd else 0.0 for v in values]


def median(values):
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def load_geojson(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["features"]


def facility_ward_weights(points_bng, wards_bng):
    """For each facility point, full weight to its containing ward and half
    weight to any other ward whose boundary is within BUFFER_M."""
    weights = []  # list of {ward: weight}
    for pt in points_bng:
        w = {}
        for ward, poly in wards_bng.items():
            if poly.contains(pt):
                w[ward] = 1.0
            elif pt.distance(poly) <= BUFFER_M:
                w[ward] = 0.5
        weights.append(w)
    return weights


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    parser.add_argument("--prefix", default="leicester", help="City slug prefixing the geojson filenames")
    args = parser.parse_args()

    lsoas = load_geojson(args.outdir / f"{args.prefix}_idaci_lsoa.geojson")
    ward_polys = load_geojson(args.outdir / f"{args.prefix}_ward_boundaries.geojson")
    pitches = load_geojson(args.outdir / f"{args.prefix}_pitches.geojson")
    schools = load_geojson(args.outdir / f"{args.prefix}_schools.geojson")

    wards_bng = {
        f["properties"]["ward"]: shp_transform(TO_BNG, shape(f["geometry"]))
        for f in ward_polys
    }
    wards = sorted(wards_bng)

    # ---- Demand ----
    d = {w: {"pop_0015": 0, "idaci_wsum": 0.0} for w in wards}
    for f in lsoas:
        p = f["properties"]
        w = p.get("ward")
        if w not in d:
            continue
        pop = p.get("pop_0015") or 0
        d[w]["pop_0015"] += pop
        if p.get("idaci_score") is not None:
            d[w]["idaci_wsum"] += p["idaci_score"] * pop

    rows = []
    for w in wards:
        pop = d[w]["pop_0015"]
        area_km2 = wards_bng[w].area / 1e6
        rows.append({
            "ward": w,
            "d1_pop_0015": pop,
            "d2_youth_density_km2": round(pop / area_km2, 1),
            "d3_idaci_popweighted": round(d[w]["idaci_wsum"] / pop, 1) if pop else None,
            "area_km2": round(area_km2, 2),
        })

    # ---- Readiness (S1 pitches, S3 schools; S2/S4 no coverage) ----
    pitch_pts = [shp_transform(TO_BNG, shape(f["geometry"])) for f in pitches]
    school_pts = [shp_transform(TO_BNG, shape(f["geometry"])) for f in schools]
    pitch_w = facility_ward_weights(pitch_pts, wards_bng)
    school_w = facility_ward_weights(school_pts, wards_bng)

    for i, r in enumerate(rows):
        w = r["ward"]
        n_pitch = sum(wt.get(w, 0) for wt in pitch_w)
        cap = sum(wt.get(w, 0) * (pitches[j]["properties"].get("total_capacity") or 0)
                  for j, wt in enumerate(pitch_w))
        n_school = sum(wt.get(w, 0) for wt in school_w)
        per_k = r["d1_pop_0015"] / 1000 if r["d1_pop_0015"] else None
        r["pitch_sites_weighted"] = n_pitch
        r["pitch_capacity_weighted"] = cap
        r["schools_weighted"] = n_school
        r["s1_pitches_per_1k_children"] = round(n_pitch / per_k, 2) if per_k else None
        r["s3_schools_per_1k_children"] = round(n_school / per_k, 2) if per_k else None
        r["readiness_indicators"] = "S1,S3"  # S2,S4 dropped: no data coverage yet

    # ---- Indices ----
    for keys, out in ((["d1_pop_0015", "d2_youth_density_km2", "d3_idaci_popweighted"], "demand_index"),
                      (["s1_pitches_per_1k_children", "s3_schools_per_1k_children"], "readiness_index")):
        zs = {k: zscores([r[k] for r in rows]) for k in keys}
        for i, r in enumerate(rows):
            for k in keys:
                r["z_" + k.split("_")[0]] = round(zs[k][i], 3)
            r[out] = round(sum(zs[k][i] for k in keys) / len(keys), 3)

    # ---- Deprivation-weighted football provision (per ward_football_provision.py method) ----
    mean_idaci = sum(r["d3_idaci_popweighted"] for r in rows) / len(rows)
    for r in rows:
        r["need_weighted"] = round(r["d1_pop_0015"] * r["d3_idaci_popweighted"] / mean_idaci, 1)
    total_need = sum(r["need_weighted"] for r in rows)
    total_supply = sum(r["pitch_capacity_weighted"] for r in rows)
    for r in rows:
        r["provision_per_1k_weighted"] = round(1000 * r["pitch_capacity_weighted"] / r["need_weighted"], 2) if r["need_weighted"] else None
        r["gap_w"] = round(r["need_weighted"] / total_need - r["pitch_capacity_weighted"] / total_supply, 4)

    dmed = median([r["demand_index"] for r in rows])
    rmed = median([r["readiness_index"] for r in rows])
    for r in rows:
        hi_d, hi_r = r["demand_index"] > dmed, r["readiness_index"] > rmed
        q = "Q1" if hi_d and not hi_r else "Q2" if hi_d else "Q3" if hi_r else "Q4"
        r["quadrant"] = q
        r["quadrant_label"], r["quadrant_action"] = QUADRANTS[q]

    # ---- Outputs ----
    out_csv = args.outdir / f"{args.prefix}_ward_readiness.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    by_ward = {r["ward"]: r for r in rows}
    features = [
        {"type": "Feature", "geometry": f["geometry"], "properties": by_ward[f["properties"]["ward"]]}
        for f in ward_polys
    ]
    out_geo = args.outdir / f"{args.prefix}_ward_readiness.geojson"
    out_geo.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")

    print(f"Wrote {out_csv} and {out_geo} ({len(rows)} wards; "
          f"quadrants: " + ", ".join(f"{q}={sum(1 for r in rows if r['quadrant'] == q)}" for q in QUADRANTS))


if __name__ == "__main__":
    main()
