#!/usr/bin/env python3
"""
Stage 0 of the city-map pipeline: fetch open mirror data and build the base
geojsons + city config for any English LAD, with zero manual steps.

  python scripts/prepare_city_data.py --city "Liverpool" [--schools-csv results.zip]

Sources (all reachable from the sandboxed environment):
  boundaries  martinjc/UK-GeoJSON (LSOA 2011 + ward 2013 by LAD)
  IDACI       humaniverse/IMD - IoD2019 IDACI numerators per LSOA11
              (counts <10 are suppressed at source; imputed at 5, the band
              midpoint). Rate = numerator / children 0-15, ranked nationally.
  population  humaniverse/demographr - ONS mid-2020 single-year ages per LSOA11
  LAD codes   data/lad_codes.csv (from mysociety/uk_local_authority_names_and_codes)

Approximations vs the official releases (record in any external use):
2019-vintage IDACI, mid-2020 denominators, suppression imputation, 2013 wards.
"""
import argparse
import csv
import io
import json
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIRRORS = REPO / "data" / "mirrors"
RAW = "https://raw.githubusercontent.com/martinjc/UK-GeoJSON/master/json"

sys.path.insert(0, str(REPO / "scripts"))
import idaci_interactive_map as m  # noqa: E402


def resolve_lad(city):
    with open(REPO / "data" / "lad_codes.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            names = [r["name"].lower()] + [a.strip().lower() for a in r["alt_names"].split(",") if a]
            if city.lower() in names:
                return r["gss_code"], r["name"]
    raise SystemExit(f"Unknown city {city!r} - check data/lad_codes.csv for the exact name")


def fetch_boundaries(lad_code):
    def get(url):
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r)
    lsoa = get(f"{RAW}/statistical/eng/lsoa_by_lad/{lad_code}.json")
    wards = get(f"{RAW}/electoral/eng/wards_by_lad/{lad_code}.json")
    return lsoa["features"], wards["features"]


def mirror_clone(repo):
    dest = MIRRORS / repo.split("/")[1]
    if not dest.exists():
        MIRRORS.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"https://github.com/{repo}", str(dest)], check=True)
    return dest


def idaci_table():
    """England-wide LSOA11 table: pop_0015, pop_total, idaci rate/rank/decile."""
    import pyreadr
    imd_dir = mirror_clone("humaniverse/IMD") / "data"
    dem_dir = mirror_clone("humaniverse/demographr") / "data"
    imd = list(pyreadr.read_r(imd_dir / "imd2019_england_lsoa11.rda").values())[0]
    ind = list(pyreadr.read_r(imd_dir / "imd2019_england_lsoa11_indicators.rda").values())[0]
    pop = list(pyreadr.read_r(dem_dir / "population20_lsoa11.rda").values())[0]
    pop["pop_0015"] = pop[[str(a) for a in range(16)]].sum(axis=1)
    df = pop.set_index("lsoa11_code")[["pop_0015", "total_population"]].join(
        ind.set_index("lsoa11_code")["income_deprivation_affecting_children_index_idaci_numerator"].rename("num"),
        how="inner",
    )
    df["num"] = df["num"].fillna(5)
    df["rate"] = df["num"] / df["pop_0015"].clip(lower=1)
    df["rank"] = df["rate"].rank(ascending=False, method="min").astype(int)
    df["decile"] = (10 * (df["rank"] - 1) // len(df) + 1).clip(upper=10)
    df["imd_decile"] = imd.set_index("lsoa11_code")["IMD_decile"]
    return df


def build_geojsons(slug, lsoa_feats, ward_feats, df, outdir):
    from shapely.geometry import shape
    n = len(df)
    wp = [(f["properties"]["WD13NM"], shape(f["geometry"])) for f in ward_feats]

    def ward_for(pt):
        for name, poly in wp:
            if poly.contains(pt):
                return name
        return min(wp, key=lambda x: x[1].distance(pt))[0]

    feats, wc = [], {}
    for f in lsoa_feats:
        code = f["properties"]["LSOA11CD"]
        if code not in df.index:
            continue
        r = df.loc[code]
        w = ward_for(shape(f["geometry"]).representative_point())
        wc[w] = wc.get(w, 0) + 1
        p0, pt = int(r["pop_0015"]), int(r["total_population"])
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
            "lsoa_code": code, "lsoa_name": f["properties"]["LSOA11NM"], "ward": w,
            "msoa_name": None, "parliamentary_constituency": None,
            "idaci_score": round(100 * float(r["rate"]), 1),
            "idaci_rank": int(r["rank"]),
            "idaci_percentile": round(100 * int(r["rank"]) / n, 1),
            "idaci_decile": int(r["decile"]),
            "idaci_quintile": (int(r["decile"]) + 1) // 2,
            "imd_score": None,
            "imd_decile": None if r["imd_decile"] != r["imd_decile"] else int(r["imd_decile"]),
            "pop_0015": p0, "pop_total": pt,
            "pct_children_0015": round(100 * p0 / pt, 1) if pt else None,
        }})
    (outdir / f"{slug}_idaci_lsoa.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    wards_out = [{"type": "Feature", "geometry": f["geometry"],
                  "properties": {"ward": f["properties"]["WD13NM"],
                                 "lsoa_count": wc.get(f["properties"]["WD13NM"], 0)}} for f in ward_feats]
    (outdir / f"{slug}_ward_boundaries.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": wards_out}))
    return len(feats), len(wards_out)


def build_schools(slug, path, lad_name, outdir):
    from shapely.geometry import Point, shape
    if path.suffix.lower() == ".zip":
        z = zipfile.ZipFile(path)
        csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        rows = list(csv.DictReader(io.TextIOWrapper(z.open(csv_name), encoding="utf-8-sig")))
    else:
        rows = list(m.load_school_rows(path))
    # GIAS LA names differ slightly from registry nice-names ("Kingston upon Hull, City of")
    las = {r.get("LA (name)", "") for r in rows}
    key = lambda s: set(re.sub(r"[^a-z ]", "", s.lower()).split())
    la = max(las, key=lambda x: len(key(x) & key(lad_name)))
    feats = m.build_school_features([r for r in rows if r.get("LA (name)") == la])
    wards = [(w["properties"]["ward"], shape(w["geometry"]))
             for w in json.loads((outdir / f"{slug}_ward_boundaries.geojson").read_text())["features"]]
    for x in feats:
        pt = Point(x["geometry"]["coordinates"])
        x["properties"]["ward"] = next((nm for nm, p in wards if p.contains(pt)),
                                       min(wards, key=lambda wp: wp[1].distance(pt))[0])
    (outdir / f"{slug}_schools.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return len(feats), la


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", help="City name as in data/lad_codes.csv (e.g. \"Liverpool\")")
    parser.add_argument("--lad-code", help="GSS code (e.g. E08000012) - overrides --city lookup")
    parser.add_argument("--slug", help="Filename slug; default derived from the city name")
    parser.add_argument("--schools-csv", type=Path, help="GIAS export (csv or zip) to build the schools layer")
    parser.add_argument("--outdir", default=REPO / "output", type=Path)
    args = parser.parse_args()

    if args.lad_code and args.city:
        lad, name = args.lad_code, args.city
    elif args.city:
        lad, name = resolve_lad(args.city)
    else:
        parser.error("--city or --lad-code required")
    slug = args.slug or re.sub(r"[^a-z]+", "-", name.lower()).strip("-")
    args.outdir.mkdir(parents=True, exist_ok=True)

    print(f"{name} ({lad}) -> slug {slug!r}")
    lsoa_feats, ward_feats = fetch_boundaries(lad)
    nl, nw = build_geojsons(slug, lsoa_feats, ward_feats, idaci_table(), args.outdir)
    print(f"  {nl} LSOAs, {nw} wards")

    if args.schools_csv:
        ns, la = build_schools(slug, args.schools_csv, name, args.outdir)
        print(f"  {ns} schools (GIAS LA {la!r})")

    cfg = REPO / "cities" / f"{slug}.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({
            "name": name, "slug": slug, "lad_code": lad,
            "inputs": {"_note": "Base data auto-built by scripts/prepare_city_data.py from GitHub "
                                "mirrors (see that script's docstring for sources and approximations)."},
            "curated": {},
        }, indent=2))
        print(f"  wrote {cfg}")
    print(f"Next: python scripts/build_city_map.py --config cities/{slug}.json --from-outputs --verify")


if __name__ == "__main__":
    main()
