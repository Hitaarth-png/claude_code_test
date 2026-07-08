"""Stage 1 — load and normalise each source into one tidy per-LSOA attribute table.

Auto-detects the relevant columns (reusing common.find_column) so it tolerates
the exact header variations in the gov.uk / ONS releases. Writes
<work_dir>/lsoa_attributes.csv keyed on lsoa_code.
"""
from __future__ import annotations

import sys

import pandas as pd

from common import clean_codes, find_column, load_config, read_table, resolve


def load_imd(path) -> pd.DataFrame:
    df = read_table(path)
    code = find_column(df.columns, ["lsoa", "code"]) or find_column(df.columns, ["lsoa"])
    cols = {
        "idaci_score": find_column(df.columns, ["idaci", "score"]),
        "imd_score": find_column(df.columns, ["imd", "score"])
        or find_column(df.columns, ["index", "multiple", "score"]),
        "idaci_decile": find_column(df.columns, ["idaci", "decile"]),
        "imd_decile": find_column(df.columns, ["imd", "decile"])
        or find_column(df.columns, ["index", "multiple", "decile"]),
    }
    if code is None or cols["idaci_score"] is None:
        raise SystemExit(f"IMD file missing LSOA code / IDACI score. Columns: {list(df.columns)}")
    out = pd.DataFrame({"lsoa_code": clean_codes(df[code])})
    for name, src in cols.items():
        if src is not None:
            out[name] = pd.to_numeric(df[src], errors="coerce")
    return out


def load_youth(path) -> pd.DataFrame:
    df = read_table(path)
    code = find_column(df.columns, ["lsoa", "code"]) or find_column(df.columns, ["lsoa"])
    val = find_column(df.columns, ["youth"]) or find_column(df.columns, ["population"]) \
        or find_column(df.columns, ["0", "15"])
    if code is None or val is None:
        raise SystemExit(f"Youth file missing LSOA code / population. Columns: {list(df.columns)}")
    return pd.DataFrame({
        "lsoa_code": clean_codes(df[code]),
        "youth_population": pd.to_numeric(df[val], errors="coerce"),
    })


def load_lookup(path) -> pd.DataFrame:
    df = read_table(path)
    code = find_column(df.columns, ["lsoa", "code"]) or find_column(df.columns, ["lsoa"], exclude=["name"])
    ward = find_column(df.columns, ["ward", "name"]) or find_column(df.columns, ["ward"], exclude=["code"]) \
        or find_column(df.columns, ["wd", "nm"])
    if code is None or ward is None:
        raise SystemExit(f"Lookup missing LSOA code / ward name. Columns: {list(df.columns)}")
    return pd.DataFrame({
        "lsoa_code": clean_codes(df[code]),
        "ward_name": df[ward].astype(str).str.strip(),
    }).drop_duplicates("lsoa_code")


def main():
    cfg = load_config()
    p = cfg["paths"]

    imd = load_imd(resolve(p["imd"]))
    youth = load_youth(resolve(p["youth_population"]))
    lookup = load_lookup(resolve(p["lsoa_ward_lookup"]))

    # Ward lookup defines the city's canonical LSOA set.
    base = lookup.merge(imd, on="lsoa_code", how="left").merge(youth, on="lsoa_code", how="left")

    for col in ("idaci_score", "imd_score", "youth_population"):
        missing = base[col].isna().sum() if col in base else len(base)
        if missing:
            print(f"Warning: {missing}/{len(base)} LSOAs missing {col}.", file=sys.stderr)

    work = resolve(cfg["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    out = work / "lsoa_attributes.csv"
    base.to_csv(out, index=False)
    print(f"Wrote {out} ({len(base)} LSOAs for {cfg['city']['name']}).")


if __name__ == "__main__":
    main()
