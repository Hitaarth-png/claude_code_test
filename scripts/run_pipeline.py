"""Run the full city-plan pipeline: ingest -> geocode -> metrics -> geojson -> map.

If the configured input files are missing, generates synthetic stand-in data
first (unless --no-sample) so the pipeline still produces a demonstrable map.
"""
from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path

from common import load_config, resolve

STAGES = ["ingest", "geocode_assets", "build_metrics", "build_geojson", "build_map"]


def inputs_present(cfg) -> bool:
    paths = [cfg["paths"]["boundaries"], cfg["paths"]["imd"],
             cfg["paths"]["youth_population"], cfg["paths"]["lsoa_ward_lookup"],
             *cfg["paths"]["assets"].values()]
    return all(resolve(p).exists() for p in paths)


def run(stage):
    print(f"\n=== {stage} ===")
    runpy.run_path(str(Path(__file__).parent / f"{stage}.py"), run_name="__main__")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-sample", action="store_true",
                    help="Fail instead of generating synthetic data when inputs are missing.")
    args = ap.parse_args()

    cfg = load_config()

    # Convert any supplied raw GIAS export into real school / youth-centre assets
    # before the sample step, so those real files pre-exist and are kept.
    gias_raw = cfg["paths"].get("gias_raw")
    if gias_raw and resolve(gias_raw).exists():
        run("prepare_gias")

    if not inputs_present(cfg):
        if args.no_sample:
            sys.exit("Input data files missing (see README.md). Aborting (--no-sample).")
        print("Some inputs missing -> fabricating only the missing ones (real files kept).")
        run("make_sample_data")

    for stage in STAGES:
        run(stage)

    print(f"\nDone. Open {resolve(cfg['output_dir']) / 'city_plan.html'}")


if __name__ == "__main__":
    main()
