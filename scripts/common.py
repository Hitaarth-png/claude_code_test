"""Shared helpers for the interactive city-plan pipeline.

Centralises config loading, column auto-detection (generalised from the original
idaci_leicester_map.py), and small numeric utilities reused across stages.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
import yaml

# Repo root = parent of the scripts/ directory that holds this file.
ROOT = Path(__file__).resolve().parent.parent

# English LSOA codes look like E01xxxxxx (2011) or E01/W01 etc.
LSOA_CODE_PATTERN = re.compile(r"^[EW]01\d{6}$")

# Env var by which the orchestrator selects the active city for each stage.
CITY_ENV = "CITY_PLAN_CITY"

ASSET_KEYS = ["schools", "football_pitches", "football_providers", "youth_mobility_centres"]


def list_cities() -> dict:
    """Return the {slug: {name, lad_code, gias_la_code}} registry from cities.yaml."""
    with open(ROOT / "cities.yaml") as fh:
        return yaml.safe_load(fh)


def load_config(path: str | Path | None = None, city: str | None = None) -> dict:
    """Load pipeline config for a city.

    City selection order: explicit `city` arg -> $CITY_PLAN_CITY -> registry
    default_city -> first registered city. Per-city data/build/output paths are
    namespaced by slug; shared raw inputs are read from `raw.*`.
    """
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path) as fh:
        cfg = yaml.safe_load(fh)

    reg = list_cities()
    slug = city or os.environ.get(CITY_ENV) or reg.get("default_city") or next(iter(reg["cities"]))
    if slug not in reg["cities"]:
        raise SystemExit(f"Unknown city '{slug}'. Known: {', '.join(reg['cities'])}")
    cfg["city"] = {**reg.get("defaults", {}), **reg["cities"][slug], "slug": slug}

    dd = f"{cfg['data_dir']}/{slug}"
    cfg["work_dir"] = f"{cfg['work_dir']}/{slug}"
    cfg["output_dir"] = f"{cfg['output_dir']}/{slug}"
    cfg["paths"] = {
        "gias_raw": cfg.get("raw", {}).get("gias"),
        "imd_raw": cfg.get("raw", {}).get("imd"),
        "boundaries": f"{dd}/lsoa_boundaries.geojson",
        "imd": f"{dd}/imd.csv",
        "youth_population": f"{dd}/youth_pop.csv",
        "lsoa_ward_lookup": f"{dd}/lsoa_ward_lookup.csv",
        "assets": {k: f"{dd}/assets/{k}.csv" for k in ASSET_KEYS},
    }
    return cfg


def resolve(cfg_path: str) -> Path:
    """Resolve a config-relative path against the repo root."""
    p = Path(cfg_path)
    return p if p.is_absolute() else ROOT / p


def find_column(columns, keywords, exclude=()):
    """Return the first column whose name contains all keywords (case-insensitive)."""
    for col in columns:
        name = str(col).lower()
        if all(k in name for k in keywords) and not any(e in name for e in exclude):
            return col
    return None


def read_table(path: Path) -> pd.DataFrame:
    """Read a CSV or Excel table."""
    if path.suffix.lower() in (".xls", ".xlsx"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def clean_codes(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip()


def minmax(series: pd.Series) -> pd.Series:
    """Scale a numeric series to 0..1 (0 if constant / all-NaN)."""
    s = pd.to_numeric(series, errors="coerce")
    lo, hi = s.min(), s.max()
    if pd.isna(lo) or pd.isna(hi) or hi == lo:
        return s.fillna(0) * 0.0
    return (s - lo) / (hi - lo)
