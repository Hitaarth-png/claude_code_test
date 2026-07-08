#!/usr/bin/env python3
"""
Gather youth population (age 0-15) for every LSOA in the City of Manchester,
tagged with the electoral ward each LSOA belongs to, and roll the figures up
to ward level.

The script takes two official, publicly available inputs (see README.md for
exact download links) and does not require any restricted or personal data --
it works purely with aggregate small-area census counts.

Inputs
------
  --age-data     Census 2021 "TS007 - Age by single year" for LSOAs
                 (Nomis, https://www.nomisweb.co.uk/datasets/c2021ts007).
                 Accepted in either shape:
                   * WIDE  - one column per single year of age
                             (Nomis bulk / "Aged under 1 year", "Aged 1 year"...)
                   * LONG  - one row per (LSOA, age) with an age column and a
                             value/count column (Nomis API .data.csv).
  --ward-lookup  ONS "LSOA (2021) to Electoral Ward to LAD Best Fit Lookup in EW"
                 (Open Geography Portal). Must contain an LSOA 2021 code column
                 (LSOA21CD), a ward code/name column (WDxxCD / WDxxNM) and a LAD
                 code column (LADxxCD).

Optional
--------
  --boundaries   LSOA 2021 boundaries (GeoJSON/Shapefile, ONS Open Geography
                 Portal). If supplied, a choropleth map of the 0-15 population
                 per LSOA is also rendered.

Outputs (written to --outdir, default ./output)
-----------------------------------------------
  manchester_youth_pop_0_15_by_lsoa.csv   one row per Manchester LSOA
  manchester_youth_pop_0_15_by_ward.csv   aggregated to ward level
  manchester_youth_pop_0_15_map.png        (only with --boundaries)
  manchester_youth_pop_0_15_lsoa.geojson   (only with --boundaries)
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

MANCHESTER_LAD_CODE = "E08000003"
MANCHESTER_LAD_NAME = "Manchester"

LSOA_CODE_PATTERN = re.compile(r"^E01\d{6}$")
# Matches "Aged 7 years", "Aged 1 year", "7", "Age 7" etc. and captures the number.
AGE_NUM_PATTERN = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")


def find_column(columns, keywords, exclude=()):
    """Return the first column whose name contains all keywords (case-insensitive)."""
    for col in columns:
        name = str(col).lower()
        if all(k in name for k in keywords) and not any(e in name for e in exclude):
            return col
    return None


def _age_from_header(header):
    """Map a single-year-of-age column header to an integer age, or None.

    Handles the Nomis TS007 convention where age 0 is labelled
    "Aged under 1 year" and later ages "Aged N year(s)".
    """
    name = str(header).lower()
    if "total" in name or "all " in name or "all categories" in name:
        return None
    if "under 1" in name:  # "Aged under 1 year" == age 0
        return 0
    # Avoid picking up a "measures: Value" style suffix number by ignoring
    # headers that clearly are not an age column.
    if "age" not in name and not name.strip().isdigit():
        return None
    m = AGE_NUM_PATTERN.search(name)
    return int(m.group(1)) if m else None


def load_age_data(path, max_age):
    """Return a DataFrame with columns [lsoa_code, pop_youth, pop_total].

    pop_youth = sum of population aged 0..max_age (inclusive) per LSOA.
    pop_total = total usual residents per LSOA (all ages) when derivable.
    """
    if path.suffix.lower() in (".xls", ".xlsx"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]

    code_col = (
        find_column(df.columns, ["lsoa", "code"])
        or find_column(df.columns, ["geography", "code"])
        or find_column(df.columns, ["lsoa21cd"])
    )
    if code_col is None:
        raise SystemExit(
            f"Could not find an LSOA code column in {path}. Columns: {list(df.columns)}"
        )

    # WIDE detection takes precedence: a genuine single-year-of-age table has
    # many columns whose *headers* encode an age (0..100+). A long table encodes
    # age as row values under a single "age" column instead.
    age_cols = {c: _age_from_header(c) for c in df.columns if c != code_col}
    wide_age_cols = [c for c, a in age_cols.items() if a is not None]

    age_col = find_column(df.columns, ["age"], exclude=["percent", "%"])
    value_col = (
        find_column(df.columns, ["observation"])
        or find_column(df.columns, ["obs_value"])
        or find_column(df.columns, ["value"])
        or find_column(df.columns, ["count"])
    )

    # LONG shape: fewer than two age-encoding headers, but a dedicated age column
    # holding one age per row plus a numeric value column.
    is_long = (
        len(wide_age_cols) < 2
        and age_col is not None
        and value_col is not None
        and df[age_col].map(_age_is_scalar).mean() > 0.5
    )

    if is_long:
        ages = df[age_col].map(_coerce_age)
        vals = pd.to_numeric(df[value_col], errors="coerce").fillna(0)
        tmp = pd.DataFrame(
            {"lsoa_code": df[code_col].astype(str).str.strip(), "age": ages, "val": vals}
        )
        youth = (
            tmp[(tmp["age"] >= 0) & (tmp["age"] <= max_age)]
            .groupby("lsoa_code")["val"].sum()
            .rename("pop_youth")
        )
        total = tmp.groupby("lsoa_code")["val"].sum().rename("pop_total")
        out = pd.concat([youth, total], axis=1).reset_index()
        out["pop_youth"] = out["pop_youth"].fillna(0).astype(int)
        out["pop_total"] = out["pop_total"].fillna(0).astype(int)
        return out

    # WIDE shape: one column per single year of age (age_cols computed above).
    youth_cols = [c for c, a in age_cols.items() if a is not None and 0 <= a <= max_age]
    all_age_cols = wide_age_cols
    if not youth_cols:
        raise SystemExit(
            "Could not identify single-year-of-age columns for ages "
            f"0-{max_age} in {path}. Columns: {list(df.columns)}"
        )

    out = pd.DataFrame({"lsoa_code": df[code_col].astype(str).str.strip()})
    out["pop_youth"] = (
        df[youth_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1).astype(int)
    )

    total_col = find_column(df.columns, ["total"]) or find_column(df.columns, ["all", "ages"])
    if total_col is not None:
        out["pop_total"] = pd.to_numeric(df[total_col], errors="coerce").fillna(0).astype(int)
    else:
        out["pop_total"] = (
            df[all_age_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1).astype(int)
        )
    return out.groupby("lsoa_code", as_index=False).sum()


def _age_is_scalar(v):
    return _coerce_age(v) is not None


def _coerce_age(v):
    s = str(v).strip().lower()
    if "under 1" in s:
        return 0
    if "total" in s or "all" in s:
        return None
    m = AGE_NUM_PATTERN.search(s)
    return int(m.group(1)) if m else None


def load_ward_lookup(path, lad_code=MANCHESTER_LAD_CODE, lad_name=MANCHESTER_LAD_NAME):
    """Return [lsoa_code, lsoa_name, ward_code, ward_name] filtered to the LAD."""
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]

    lsoa_code_col = find_column(df.columns, ["lsoa21cd"]) or find_column(
        df.columns, ["lsoa", "cd"], exclude=["nm"]
    ) or find_column(df.columns, ["lsoa", "code"])
    lsoa_name_col = find_column(df.columns, ["lsoa21nm"]) or find_column(
        df.columns, ["lsoa", "nm"], exclude=["nmw"]
    ) or find_column(df.columns, ["lsoa", "name"])
    ward_code_col = find_column(df.columns, ["wd", "cd"]) or find_column(df.columns, ["ward", "code"])
    ward_name_col = find_column(df.columns, ["wd", "nm"]) or find_column(df.columns, ["ward", "name"])
    lad_code_col = find_column(df.columns, ["lad", "cd"]) or find_column(df.columns, ["la", "code"])
    lad_name_col = find_column(df.columns, ["lad", "nm"]) or find_column(df.columns, ["la", "name"])

    if lsoa_code_col is None or ward_code_col is None:
        raise SystemExit(
            f"Could not find LSOA / ward columns in {path}. Columns: {list(df.columns)}"
        )

    # Filter to the target local authority (by code if present, else name).
    if lad_code_col is not None and df[lad_code_col].astype(str).str.contains(lad_code).any():
        df = df[df[lad_code_col].astype(str).str.strip() == lad_code]
    elif lad_name_col is not None:
        df = df[df[lad_name_col].astype(str).str.contains(lad_name, case=False, na=False)]
    else:
        raise SystemExit(
            "Ward lookup has no LAD code/name column to filter Manchester. "
            f"Columns: {list(df.columns)}"
        )

    if df.empty:
        raise SystemExit(
            f"No rows matched Manchester ({lad_code}) in the ward lookup. "
            "Check that you downloaded the England & Wales lookup."
        )

    rename = {lsoa_code_col: "lsoa_code", ward_code_col: "ward_code"}
    keep = ["lsoa_code", "ward_code"]
    if lsoa_name_col:
        rename[lsoa_name_col] = "lsoa_name"
        keep.append("lsoa_name")
    if ward_name_col:
        rename[ward_name_col] = "ward_name"
        keep.append("ward_name")
    out = df.rename(columns=rename)[keep].copy()
    out["lsoa_code"] = out["lsoa_code"].astype(str).str.strip()
    return out.drop_duplicates("lsoa_code")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--age-data", required=True, type=Path, help="Census 2021 TS007 LSOA age CSV/Excel")
    parser.add_argument("--ward-lookup", required=True, type=Path, help="ONS LSOA21->Ward->LAD best-fit lookup CSV")
    parser.add_argument("--boundaries", type=Path, help="LSOA 2021 boundaries GeoJSON/Shapefile (optional, enables map)")
    parser.add_argument("--lad-code", default=MANCHESTER_LAD_CODE, help="Local authority district code (default Manchester)")
    parser.add_argument("--max-age", default=15, type=int, help="Upper age bound, inclusive (default 15)")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    ages = load_age_data(args.age_data, args.max_age)
    wards = load_ward_lookup(args.ward_lookup, lad_code=args.lad_code)

    merged = wards.merge(ages, on="lsoa_code", how="left")

    missing = merged["pop_youth"].isna().sum()
    if missing:
        print(
            f"Warning: {missing} of {len(merged)} Manchester LSOAs had no match in the "
            "age data. Check that both files use 2021 LSOA codes.",
            file=sys.stderr,
        )
    merged["pop_youth"] = merged["pop_youth"].fillna(0).astype(int)
    merged["pop_total"] = merged["pop_total"].fillna(0).astype(int)
    merged["pct_youth"] = (
        (merged["pop_youth"] / merged["pop_total"].replace(0, pd.NA) * 100).round(1)
    )

    sort_cols = [c for c in ("ward_name", "lsoa_code") if c in merged.columns]
    by_lsoa = merged.sort_values(sort_cols)
    lsoa_out = args.outdir / "manchester_youth_pop_0_15_by_lsoa.csv"
    by_lsoa.to_csv(lsoa_out, index=False)

    group_keys = [c for c in ("ward_code", "ward_name") if c in merged.columns]
    by_ward = (
        merged.groupby(group_keys, as_index=False)
        .agg(n_lsoas=("lsoa_code", "nunique"), pop_youth=("pop_youth", "sum"), pop_total=("pop_total", "sum"))
    )
    by_ward["pct_youth"] = (
        (by_ward["pop_youth"] / by_ward["pop_total"].replace(0, pd.NA) * 100).round(1)
    )
    by_ward = by_ward.sort_values("pop_youth", ascending=False)
    ward_out = args.outdir / "manchester_youth_pop_0_15_by_ward.csv"
    by_ward.to_csv(ward_out, index=False)

    print(f"Manchester ({args.lad_code}): {len(merged)} LSOAs across {by_ward.shape[0]} wards")
    print(f"Total population aged 0-{args.max_age}: {int(merged['pop_youth'].sum()):,}")
    print(f"Wrote {lsoa_out}")
    print(f"Wrote {ward_out}")

    if args.boundaries:
        render_map(merged, args)


def render_map(merged, args):
    import geopandas as gpd
    import matplotlib.pyplot as plt

    gdf = gpd.read_file(args.boundaries)
    lsoa_col = None
    for candidate in ("LSOA21CD", "LSOA11CD", "lsoa21cd", "lsoa11cd"):
        if candidate in gdf.columns:
            lsoa_col = candidate
            break
    if lsoa_col is None:
        lsoa_col = find_column(gdf.columns, ["lsoa"], exclude=["name", "nm"])
    if lsoa_col is None:
        print("Could not find an LSOA code column in boundaries; skipping map.", file=sys.stderr)
        return

    gdf = gdf.rename(columns={lsoa_col: "lsoa_code"})
    gdf["lsoa_code"] = gdf["lsoa_code"].astype(str).str.strip()
    gdf = gdf.merge(merged, on="lsoa_code", how="inner")
    if gdf.empty:
        print("No boundary geometries matched Manchester LSOAs; skipping map.", file=sys.stderr)
        return

    gdf.to_crs(epsg=27700).to_file(args.outdir / "manchester_youth_pop_0_15_lsoa.geojson", driver="GeoJSON")
    fig, ax = plt.subplots(1, 1, figsize=(11, 11))
    gdf.to_crs(epsg=27700).plot(
        column="pop_youth",
        cmap="YlGnBu",
        linewidth=0.3,
        edgecolor="grey",
        legend=True,
        legend_kwds={"label": f"Population aged 0-{args.max_age} (Census 2021)", "shrink": 0.6},
        missing_kwds={"color": "lightgrey", "label": "No data"},
        ax=ax,
    )
    ax.set_title(f"Youth population (aged 0-{args.max_age}) by LSOA\nCity of Manchester", fontsize=14)
    ax.set_axis_off()
    fig.tight_layout()
    out_png = args.outdir / "manchester_youth_pop_0_15_map.png"
    fig.savefig(out_png, dpi=200)
    print(f"Wrote {out_png}")


if __name__ == "__main__":
    main()
