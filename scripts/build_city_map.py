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
    parser.add_argument("--verify", action="store_true", help="Sanity-check the built HTML and layers; non-zero exit on failure")
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

    # ward readiness needs at least one facility layer (schools or pitches)
    if any((args.outdir / f"{slug}_{n}.geojson").exists() for n in ("schools", "pitches")):
        subprocess.run(
            [sys.executable, str(SCRIPTS / "ward_readiness.py"),
             "--outdir", str(args.outdir), "--prefix", slug],
            check=True,
        )
    else:
        print(f"No {slug} facility layers yet - skipping ward readiness (LSOA map only)")
    m.rebuild_from_outputs(args.outdir, prefix=slug, city=city)

    if args.verify:
        import json as _json
        import re as _re
        html = (args.outdir / f"{slug}_idaci_interactive_map.html").read_text(encoding="utf-8")
        leftover = _re.findall(r"__[A-Z_]+__", html)
        lsoas = _json.loads((args.outdir / f"{slug}_idaci_lsoa.geojson").read_text())["features"]
        wards = {f["properties"]["ward"] for f in lsoas}
        problems = []
        if leftover:
            problems.append(f"unfilled placeholders: {leftover}")
        if not lsoas:
            problems.append("no LSOA features")
        if not wards:
            problems.append("no wards assigned")
        if f"<h1>{city} " not in html:
            problems.append("city name missing from HTML")
        if problems:
            raise SystemExit(f"VERIFY FAILED for {slug}: " + "; ".join(problems))
        print(f"VERIFY OK: {len(lsoas)} LSOAs, {len(wards)} wards, {len(html)//1024} KiB HTML")


if __name__ == "__main__":
    main()
