"""Shared helpers for the interactive city-plan pipeline.

Centralises config loading, column auto-detection (generalised from the original
idaci_leicester_map.py), and small numeric utilities reused across stages.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import yaml

# Repo root = parent of the scripts/ directory that holds this file.
ROOT = Path(__file__).resolve().parent.parent

# English LSOA codes look like E01xxxxxx (2011) or E01/W01 etc.
LSOA_CODE_PATTERN = re.compile(r"^[EW]01\d{6}$")


def load_config(path: str | Path | None = None) -> dict:
    """Load config.yaml (repo root by default)."""
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path) as fh:
        return yaml.safe_load(fh)


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
