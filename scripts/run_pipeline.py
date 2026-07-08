"""Run the full city-plan pipeline for one city:
prepare_imd -> prepare_gias -> (synthetic fill) -> ingest -> geocode -> metrics
-> geojson -> map.

Select the city with --city <slug> (see cities.yaml); missing inputs are filled
with clearly-labelled synthetic data unless --no-sample.
"""
from __future__ import annotations

import argparse
import os
import runpy
import sys
from pathlib import Path

from common import CITY_ENV, load_config, resolve

STAGES = ["ingest", "geocode_assets", "build_metrics", "build_geojson", "build_map"]


def inputs_present(cfg) -> bool:
    paths = [cfg["paths"]["boundaries"], cfg["paths"]["imd"],
             cfg["paths"]["youth_population"], cfg["paths"]["lsoa_ward_lookup"],
             *cfg["paths"]["assets"].values()]
    return all(resolve(p).exists() for p in paths)


def run(stage):
    print(f"\n=== {stage} ===")
    runpy.run_path(str(Path(__file__).parent / f"{stage}.py"), run_name="__main__")


def run_for_city(slug: str, no_sample: bool = False) -> Path:
    """Build the full plan for one city; returns the output HTML path."""
    os.environ[CITY_ENV] = slug
    cfg = load_config(city=slug)
    print(f"\n########## {cfg['city']['name']} ({slug}) ##########")

    # Real IoD supplies both the IMD table and 2021 boundaries (run first);
    # GIAS supplies real schools / youth-mobility centres.
    if cfg["paths"].get("imd_raw") and resolve(cfg["paths"]["imd_raw"]).exists():
        run("prepare_imd")
    if cfg["paths"].get("gias_raw") and resolve(cfg["paths"]["gias_raw"]).exists():
        run("prepare_gias")

    if not inputs_present(cfg):
        if no_sample:
            sys.exit(f"[{slug}] inputs missing (see ROUTINE.md). Aborting (--no-sample).")
        print("Some inputs missing -> fabricating only the missing ones (real files kept).")
        run("make_sample_data")

    for stage in STAGES:
        run(stage)

    out = resolve(cfg["output_dir"]) / "city_plan.html"
    print(f"\nDone. Open {out}")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--city", default=None, help="City slug from cities.yaml (default: registry default_city)")
    ap.add_argument("--no-sample", action="store_true",
                    help="Fail instead of generating synthetic data when inputs are missing.")
    args = ap.parse_args()
    slug = args.city or load_config()["city"]["slug"]
    run_for_city(slug, no_sample=args.no_sample)


if __name__ == "__main__":
    main()
