"""Stage 1 — load and normalise each source into one tidy per-LSOA attribute table.

Auto-detects the relevant columns (reusing common.find_column) so it tolerates
the exact header variations in the gov.uk / ONS releases. Writes
<work_dir>/lsoa_attributes.csv keyed on lsoa_code.
"""
from __future__ import annotations

import sys

import pandas as pd

from common import clean_codes, find_column, load_config, read_table, resolve


def _deprivation_score(df, keywords):
    """A 0-1 deprivation magnitude (1 = most deprived) from the best available
    field: a real score, else national rank (1 = most deprived), else decile."""
    score = find_column(df.columns, keywords + ["score"])
    if score is not None:
        return pd.to_numeric(df[score], errors="coerce")
    rank = find_column(df.columns, keywords + ["rank"])
    if rank is not None:
        r = pd.to_numeric(df[rank], errors="coerce")
        return (r.max() - r) / (r.max() - r.min())          # invert: rank 1 -> 1.0
    dec = find_column(df.columns, keywords + ["decile"])
    if dec is not None:
        d = pd.to_numeric(df[dec], errors="coerce")
        return (10 - d) / 9                                   # decile 1 -> 1.0
    return None


def load_imd(path) -> pd.DataFrame:
    df = read_table(path)
    code = find_column(df.columns, ["lsoa", "code"]) or find_column(df.columns, ["lsoa"])
    if code is None:
        raise SystemExit(f"IMD file missing LSOA code. Columns: {list(df.columns)}")
    out = pd.DataFrame({"lsoa_code": clean_codes(df[code])})
    out["idaci_score"] = _deprivation_score(df, ["idaci"])
    out["imd_score"] = _deprivation_score(df, ["multiple"]) if find_column(df.columns, ["multiple"]) \
        else _deprivation_score(df, ["imd"])
    # Carry deciles through for display (1 = most deprived).
    idaci_dec = find_column(df.columns, ["idaci", "decile"])
    imd_dec = find_column(df.columns, ["multiple", "decile"]) or find_column(df.columns, ["imd", "decile"])
    if idaci_dec is not None:
        out["idaci_decile"] = pd.to_numeric(df[idaci_dec], errors="coerce")
    if imd_dec is not None:
        out["imd_decile"] = pd.to_numeric(df[imd_dec], errors="coerce")
    if out["idaci_score"] is None or out["idaci_score"].isna().all():
        raise SystemExit(f"IMD file has no usable IDACI score/rank/decile. Columns: {list(df.columns)}")
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
    name = find_column(df.columns, ["lsoa", "name"])
    if code is None or ward is None:
        raise SystemExit(f"Lookup missing LSOA code / ward name. Columns: {list(df.columns)}")
    out = pd.DataFrame({
        "lsoa_code": clean_codes(df[code]),
        "ward_name": df[ward].astype(str).str.strip(),
    })
    if name is not None:
        out["lsoa_name"] = df[name].astype(str).str.strip()
    return out.drop_duplicates("lsoa_code")


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
