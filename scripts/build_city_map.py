#!/usr/bin/env python3
"""
One-command pipeline: build the full interactive city map (deprivation
choropleth + facility overlays + ward readiness / provision-gap layers)
from a cities/<slug>.json config. See WORKFLOW.md for the data each stage
expects and how to prepare a new city.

Stages:
  1. Source CSVs -> <slug>_*.geojson + base HTML   (skip with --from-outputs)
  2. Ward readiness, provision gap & quadrants     (scripts/ward_readiness.py)
  3. Final HTML rebuild with the ward layers embedded
"""
import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS))
import idaci_interactive_map as m  # noqa: E402

INPUT_FLAGS = [
    ("--schools", "schools_csv"),
    ("--pitches", "pitches_csv"),
    ("--youth-centres", "youth_centres_csv"),
    ("--social-mobility-partners", "social_mobility_partners_csv"),
    ("--football-providers", "football_providers_csv"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="cities/<slug>.json")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    parser.add_argument(
        "--from-outputs",
        action="store_true",
        help="Skip stage 1; recompute wards and rebuild HTML from existing <slug>_*.geojson",
    )
    args = parser.parse_args()

    cfg = m.apply_city_config(args.config)
    slug, city = cfg["slug"], cfg["name"]

    if not args.from_outputs:
        inputs = cfg.get("inputs", {})
        cmd = [
            sys.executable, str(SCRIPTS / "idaci_interactive_map.py"),
            "--input", inputs["idaci_csv"],
            "--outdir", str(args.outdir),
            "--slug", slug, "--city", city, "--city-config", str(args.config),
        ]
        for flag, key in INPUT_FLAGS:
            if inputs.get(key):
                cmd += [flag, inputs[key]]
        subprocess.run(cmd, check=True)

    subprocess.run(
        [sys.executable, str(SCRIPTS / "ward_readiness.py"),
         "--outdir", str(args.outdir), "--prefix", slug],
        check=True,
    )
    m.rebuild_from_outputs(args.outdir, prefix=slug, city=city)


if __name__ == "__main__":
    main()
