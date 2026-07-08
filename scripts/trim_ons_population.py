"""Shrink a large ONS LSOA population-estimates file to the tiny input the
pipeline needs: `lsoa_code, youth_population_0_15`.

Run this LOCALLY on the full ONS file (too big to upload), then upload / drop the
small output at `paths.youth_population`.

Usage:
    python scripts/trim_ons_population.py <ons_file> [--sheet SHEET]
        [--min-age 0] [--max-age 15] [--out data/youth_pop.csv]

Handles CSV or Excel, auto-detects the header row, the LSOA code column, and the
single-year-of-age columns (headers like 0..90, "Age 0", "90+"). If a boundary
file already exists it keeps only this city's LSOAs so the output stays tiny.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import LSOA_CODE_PATTERN, clean_codes, find_column, load_config, resolve


def age_of(col):
    """Parse a single year of age from a header like 0, '0', '0.0', 'Age 5', '90+'."""
    s = str(col).strip().lower().replace("age", "").strip().rstrip("+").strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else None


def read_with_header_detection(path: Path, sheet):
    """Read the sheet/CSV, locating the header row that contains an LSOA code col."""
    raw = (pd.read_excel(path, sheet_name=sheet, header=None)
           if path.suffix.lower() in (".xls", ".xlsx")
           else pd.read_csv(path, header=None))
    for i in range(min(15, len(raw))):
        row = raw.iloc[i].astype(str).str.lower()
        if row.str.contains("lsoa").any():
            df = raw.iloc[i + 1:].copy()
            df.columns = raw.iloc[i].astype(str).str.strip()
            return df.reset_index(drop=True)
    # Fall back to a normal read (header already on row 0).
    return (pd.read_excel(path, sheet_name=sheet)
            if path.suffix.lower() in (".xls", ".xlsx") else pd.read_csv(path))


def age_columns(cols, lo, hi):
    return [c for c in cols if (a := age_of(c)) is not None and lo <= a <= hi]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ons_file", type=Path)
    ap.add_argument("--sheet", default=0, help="Excel sheet name/index (default first)")
    ap.add_argument("--min-age", type=int, default=0)
    ap.add_argument("--max-age", type=int, default=15)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    cfg = load_config()
    out = args.out or resolve(cfg["paths"]["youth_population"])

    df = read_with_header_detection(args.ons_file, args.sheet)
    code_col = find_column(df.columns, ["lsoa", "code"]) or find_column(df.columns, ["area", "code"]) \
        or find_column(df.columns, ["lsoa"])
    if code_col is None:
        raise SystemExit(f"No LSOA code column found. Columns: {list(df.columns)[:20]}")

    ages = age_columns(df.columns, args.min_age, args.max_age)
    if not ages:
        raise SystemExit(
            f"No single-year-of-age columns {args.min_age}-{args.max_age} found. "
            f"Columns: {list(df.columns)[:30]}")

    codes = clean_codes(df[code_col])
    youth = df[ages].apply(pd.to_numeric, errors="coerce").sum(axis=1)
    result = pd.DataFrame({"lsoa_code": codes, "youth_population_0_15": youth.round().astype("Int64")})
    result = result[result["lsoa_code"].str.match(LSOA_CODE_PATTERN, na=False)].dropna()

    # Keep only this city's LSOAs if we already know them.
    bpath = resolve(cfg["paths"]["boundaries"])
    if bpath.exists():
        import geopandas as gpd
        keep = set(clean_codes(gpd.read_file(bpath)["lsoa_code"]))
        result = result[result["lsoa_code"].isin(keep)]

    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)
    print(f"Summed ages {args.min_age}-{args.max_age} from {len(ages)} columns.")
    print(f"Wrote {len(result)} LSOAs -> {out}")


if __name__ == "__main__":
    main()
