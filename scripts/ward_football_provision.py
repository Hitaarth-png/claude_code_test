#!/usr/bin/env python3
"""
Calculate and map deprivation-weighted football provision by ward for Manchester.

"Provision" = how much football capacity is available in each ward relative to how
much is needed, where need is driven by the number of children and how deprived the
ward is. All inputs are public, aggregate datasets - no participant data is used.

Method (per ward w):
  supply_w     = sum over facilities in w of  pitch_count * capacity_factor[pitch_type]
  weight_w     = ward IDACI score / mean ward IDACI      (deprivation multiplier ~1)
  need_w       = child_pop_w * weight_w                  (deprivation-weighted demand)
  provision_w  = supply_w / need_w                       (places per weighted child)
  gap_w        = need_w/sum(need) - supply_w/sum(supply) (share of need - share of supply)
                 positive gap => ward carries more need than supply => under-served

Inputs (see README.md for exact download links):
  --facilities        Sport England Active Places export (CSV): facility type/sub-type
                      + latitude/longitude (one row per facility, optional count column).
  --boundaries        Ward boundaries GeoJSON/Shapefile (ONS Open Geography Portal),
                      with a ward code property (e.g. "WD24CD").
  --population        Ward-level child population (CSV) with a ward code column and
                      either age columns or a single pre-summed child-population column.
  --idaci             IDACI (or IMD) scores per LSOA (English Indices of Deprivation
                      2019, gov.uk).
  --lsoa-ward-lookup  LSOA -> ward best-fit lookup (CSV, ONS) with LSOA + ward codes.

Output (to --outdir):
  manchester_ward_provision.png       choropleth of the provision gap by ward
  manchester_ward_provision.csv       per-ward supply / need / provision / gap / rank
  manchester_ward_provision.geojson   same, with geometry
"""
import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import TwoSlopeNorm

MANCHESTER_LAD_CODE = "E08000003"
MANCHESTER_LAD_NAME = "Manchester"

LSOA_CODE_PATTERN = re.compile(r"^E01\d{6}$")
WARD_CODE_PATTERN = re.compile(r"^E05\d{6}$")

# BFF serves children aged 3-18; ward population tables rarely carry that exact band,
# so we sum whatever single ages / bands fall inside this range (see load_population).
MIN_AGE, MAX_AGE = 3, 18

# Rough simultaneous participant places per pitch, by facility type. These are tunable
# modelling assumptions, not facts - adjust in one place if you have better guidance.
# Keys are lowercase substrings matched against the Active Places facility type text.
CAPACITY_FACTORS = {
    "artificial": 22,   # 3G / AGP full-size
    "3g": 22,
    "agp": 22,
    "grass": 22,        # full-size grass pitch
    "small sided": 10,  # small-sided (indoor/outdoor)
    "muga": 10,         # multi-use games area
    "indoor": 10,
}
DEFAULT_CAPACITY = 16  # football pitch that matched no specific type

# Facility rows we consider "football provision".
FOOTBALL_KEYWORDS = ("football", "grass pitch", "artificial grass", "3g", "agp", "muga", "small sided")


def find_column(columns, keywords, exclude=()):
    """Return the first column whose name contains all keywords (case-insensitive)."""
    for col in columns:
        name = str(col).lower()
        if all(k in name for k in keywords) and not any(e in name for e in exclude):
            return col
    return None


# --------------------------------------------------------------------------- boundaries
def load_boundaries(path, lad_code=MANCHESTER_LAD_CODE, lad_name=MANCHESTER_LAD_NAME):
    """Load ward boundaries and filter to Manchester (by LAD code, LAD name, or E05 prefix)."""
    gdf = gpd.read_file(path)

    ward_code_col = find_column(gdf.columns, ["ward", "code"]) or find_column(
        gdf.columns, ["wd"], exclude=["name", "nm"]
    )
    if ward_code_col is None:
        for candidate in ("WD24CD", "WD23CD", "WD22CD", "WD21CD"):
            if candidate in gdf.columns:
                ward_code_col = candidate
                break
    if ward_code_col is None:
        raise SystemExit(f"Could not find a ward code column in boundaries. Columns: {list(gdf.columns)}")

    ward_name_col = find_column(gdf.columns, ["ward", "name"]) or find_column(gdf.columns, ["wd", "nm"])

    gdf = gdf.rename(columns={ward_code_col: "ward_code"})
    gdf["ward_code"] = gdf["ward_code"].astype(str).str.strip()
    if ward_name_col:
        gdf = gdf.rename(columns={ward_name_col: "ward_name"})

    lad_col = find_column(gdf.columns, ["lad", "cd"]) or find_column(gdf.columns, ["la", "code"])
    if lad_col and gdf[lad_col].astype(str).str.contains(lad_code).any():
        gdf = gdf[gdf[lad_col].astype(str) == lad_code]
    else:
        lad_name_col = find_column(gdf.columns, ["lad", "nm"]) or find_column(gdf.columns, ["la", "name"])
        if lad_name_col and gdf[lad_name_col].astype(str).str.contains(lad_name, case=False).any():
            gdf = gdf[gdf[lad_name_col].astype(str).str.contains(lad_name, case=False)]
        else:
            gdf = gdf[gdf["ward_code"].str.match(WARD_CODE_PATTERN)]

    if gdf.empty:
        raise SystemExit(
            "No wards matched Manchester in the boundary file. Pass a file pre-filtered "
            "to Manchester, or check the ward/LAD code columns."
        )

    keep = ["ward_code", "geometry"] + (["ward_name"] if "ward_name" in gdf.columns else [])
    return gdf[keep].to_crs(epsg=27700)


# --------------------------------------------------------------------------- facilities
def classify_capacity(type_text):
    """Map an Active Places facility-type string to a participant-capacity factor."""
    name = str(type_text).lower()
    for keyword, factor in CAPACITY_FACTORS.items():
        if keyword in name:
            return factor
    return DEFAULT_CAPACITY


def load_facilities(path, wards):
    """Read Active Places facilities, keep football pitches, and sum capacity per ward."""
    df = pd.read_csv(path)

    type_col = (
        find_column(df.columns, ["facility", "type"])
        or find_column(df.columns, ["sub", "type"])
        or find_column(df.columns, ["type"])
    )
    lat_col = find_column(df.columns, ["lat"])
    lon_col = find_column(df.columns, ["long"]) or find_column(df.columns, ["lon"])
    count_col = find_column(df.columns, ["count"]) or find_column(df.columns, ["number", "pitch"])

    if type_col is None or lat_col is None or lon_col is None:
        raise SystemExit(
            "Could not auto-detect facility type / latitude / longitude columns in "
            f"{path}. Found columns: {list(df.columns)}"
        )

    df = df.dropna(subset=[lat_col, lon_col]).copy()
    type_text = df[type_col].astype(str).str.lower()
    df = df[type_text.apply(lambda t: any(k in t for k in FOOTBALL_KEYWORDS))].copy()
    if df.empty:
        raise SystemExit(f"No football facilities matched in {path} (checked column '{type_col}').")

    df["_count"] = pd.to_numeric(df[count_col], errors="coerce").fillna(1) if count_col else 1
    df["_capacity"] = df[type_col].apply(classify_capacity) * df["_count"]

    points = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lon_col], df[lat_col]),
        crs="EPSG:4326",
    ).to_crs(epsg=27700)

    joined = gpd.sjoin(points, wards[["ward_code", "geometry"]], how="inner", predicate="within")
    supply = joined.groupby("ward_code")["_capacity"].sum().rename("supply")
    return supply


# --------------------------------------------------------------------------- population
def _age_from_header(header):
    """Extract the age a population column refers to, if any (single age or range start)."""
    name = str(header).lower()
    nums = [int(n) for n in re.findall(r"\d+", name)]
    if not nums:
        return None
    if "age" in name or "aged" in name or "year" in name:
        return nums  # ages referenced explicitly
    # bare numeric-ish header (e.g. "0", "1", ... single-year-of-age table)
    if re.fullmatch(r"\D*\d+\D*", name):
        return nums
    return None


def load_population(path, pop_column=None):
    """Return a Series of child population per ward code (summed over ages 3-18)."""
    df = pd.read_csv(path)

    ward_col = find_column(df.columns, ["ward", "code"]) or find_column(df.columns, ["wd"], exclude=["name", "nm"])
    if ward_col is None:
        for col in df.columns:
            if df[col].astype(str).str.match(WARD_CODE_PATTERN).any():
                ward_col = col
                break
    if ward_col is None:
        raise SystemExit(f"Could not find a ward code column in {path}. Columns: {list(df.columns)}")

    codes = df[ward_col].astype(str).str.strip()

    if pop_column:
        if pop_column not in df.columns:
            raise SystemExit(f"--pop-column '{pop_column}' not found. Columns: {list(df.columns)}")
        values = pd.to_numeric(df[pop_column], errors="coerce").fillna(0)
        return pd.Series(values.values, index=codes.values, name="child_pop").groupby(level=0).sum()

    # Auto-sum age columns whose referenced ages fall inside [MIN_AGE, MAX_AGE].
    age_cols = []
    for col in df.columns:
        ages = _age_from_header(col)
        if ages and any(MIN_AGE <= a <= MAX_AGE for a in ages):
            age_cols.append(col)
    if not age_cols:
        raise SystemExit(
            f"Could not auto-detect age columns in [{MIN_AGE},{MAX_AGE}] in {path}. "
            f"Pass --pop-column with a pre-summed child-population column. Columns: {list(df.columns)}"
        )
    print(f"Summing {len(age_cols)} age columns for child population: {age_cols}", file=sys.stderr)
    values = df[age_cols].apply(pd.to_numeric, errors="coerce").fillna(0).sum(axis=1)
    return pd.Series(values.values, index=codes.values, name="child_pop").groupby(level=0).sum()


# --------------------------------------------------------------------------- deprivation
def load_idaci(path):
    """Load per-LSOA IDACI (or IMD) scores. Mirrors idaci_leicester_map.load_idaci."""
    if path.suffix.lower() in (".xls", ".xlsx"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)

    code_col = find_column(df.columns, ["lsoa", "code"])
    score_col = find_column(df.columns, ["idaci", "score"]) or find_column(df.columns, ["imd", "score"])
    if code_col is None or score_col is None:
        raise SystemExit(
            f"Could not auto-detect LSOA code / deprivation score columns in {path}. "
            f"Found columns: {list(df.columns)}"
        )
    out = df[[code_col, score_col]].copy()
    out.columns = ["lsoa_code", "idaci_score"]
    out["lsoa_code"] = out["lsoa_code"].astype(str).str.strip()
    out["idaci_score"] = pd.to_numeric(out["idaci_score"], errors="coerce")
    return out


def aggregate_deprivation_to_wards(idaci, lookup_path, child_pop):
    """Aggregate LSOA IDACI up to wards via a best-fit lookup, population-weighted."""
    lookup = pd.read_csv(lookup_path)
    lsoa_col = find_column(lookup.columns, ["lsoa", "cd"]) or find_column(lookup.columns, ["lsoa", "code"])
    ward_col = find_column(lookup.columns, ["ward", "cd"]) or find_column(lookup.columns, ["wd", "cd"]) \
        or find_column(lookup.columns, ["ward", "code"])
    if lsoa_col is None or ward_col is None:
        raise SystemExit(
            f"Could not find LSOA and ward code columns in the lookup {lookup_path}. "
            f"Columns: {list(lookup.columns)}"
        )
    lookup = lookup[[lsoa_col, ward_col]].rename(columns={lsoa_col: "lsoa_code", ward_col: "ward_code"})
    lookup["lsoa_code"] = lookup["lsoa_code"].astype(str).str.strip()
    lookup["ward_code"] = lookup["ward_code"].astype(str).str.strip()

    merged = lookup.merge(idaci, on="lsoa_code", how="left")
    # Simple mean of LSOA scores within each ward (LSOAs are similar-sized populations).
    ward_idaci = merged.groupby("ward_code")["idaci_score"].mean().rename("idaci_score")
    return ward_idaci


# --------------------------------------------------------------------------- provision
def compute_provision(supply, child_pop, ward_idaci):
    """Combine supply, population and deprivation into per-ward provision metrics."""
    df = pd.DataFrame({"child_pop": child_pop}).join([supply, ward_idaci])
    df["supply"] = df["supply"].fillna(0)

    mean_idaci = df["idaci_score"].mean(skipna=True)
    df["deprivation_weight"] = df["idaci_score"] / mean_idaci if mean_idaci else 1.0
    df["need"] = df["child_pop"] * df["deprivation_weight"]

    df["provision"] = df["supply"] / df["need"].where(df["need"] > 0)

    total_need = df["need"].sum(skipna=True)
    total_supply = df["supply"].sum(skipna=True)
    need_share = df["need"] / total_need if total_need else 0
    supply_share = df["supply"] / total_supply if total_supply else 0
    df["gap"] = need_share - supply_share  # positive => under-served

    df["rank"] = df["gap"].rank(ascending=False, method="min").astype("Int64")  # 1 = most under-served
    return df.sort_values("gap", ascending=False)


# --------------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--facilities", required=True, type=Path, help="Sport England Active Places CSV")
    parser.add_argument("--boundaries", required=True, type=Path, help="Ward boundaries GeoJSON/Shapefile")
    parser.add_argument("--population", required=True, type=Path, help="Ward child-population CSV")
    parser.add_argument("--idaci", required=True, type=Path, help="IDACI/IMD scores per LSOA (CSV/Excel)")
    parser.add_argument("--lsoa-ward-lookup", required=True, type=Path, help="LSOA->ward best-fit lookup CSV")
    parser.add_argument("--pop-column", default=None, help="Optional pre-summed child-population column name")
    parser.add_argument("--outdir", default=Path("output"), type=Path)
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    wards = load_boundaries(args.boundaries)
    supply = load_facilities(args.facilities, wards)
    child_pop = load_population(args.population, args.pop_column)
    idaci = load_idaci(args.idaci)
    ward_idaci = aggregate_deprivation_to_wards(idaci, args.lsoa_ward_lookup, child_pop)

    metrics = compute_provision(supply, child_pop, ward_idaci)

    merged = wards.merge(metrics, left_on="ward_code", right_index=True, how="left")

    missing_pop = merged["child_pop"].isna().sum()
    if missing_pop:
        print(f"Warning: {missing_pop} of {len(merged)} wards have no population match.", file=sys.stderr)
    no_supply = int((merged["supply"].fillna(0) == 0).sum())
    if no_supply:
        print(f"Note: {no_supply} of {len(merged)} wards have no football facilities (supply=0).", file=sys.stderr)

    # Outputs
    merged.to_file(args.outdir / "manchester_ward_provision.geojson", driver="GeoJSON")
    merged.drop(columns="geometry").to_csv(args.outdir / "manchester_ward_provision.csv", index=False)

    # Choropleth of the provision gap: red = under-served (high need, low supply).
    fig, ax = plt.subplots(1, 1, figsize=(11, 11))
    gap = merged["gap"]
    span = max(abs(gap.min(skipna=True) or 0), abs(gap.max(skipna=True) or 0)) or 1e-6
    merged.plot(
        column="gap",
        cmap="RdBu_r",
        norm=TwoSlopeNorm(vcenter=0, vmin=-span, vmax=span),
        linewidth=0.3,
        edgecolor="grey",
        legend=True,
        legend_kwds={"label": "Provision gap (share of need - share of supply)", "shrink": 0.6},
        missing_kwds={"color": "lightgrey", "label": "No data"},
        ax=ax,
    )
    ax.set_title(
        "Deprivation-weighted football provision gap by ward\nManchester (red = under-served)",
        fontsize=14,
    )
    ax.set_axis_off()
    fig.tight_layout()

    out_png = args.outdir / "manchester_ward_provision.png"
    fig.savefig(out_png, dpi=200)
    print(f"Wrote {out_png}")
    print(f"Wrote {args.outdir / 'manchester_ward_provision.csv'}")
    print(f"Wrote {args.outdir / 'manchester_ward_provision.geojson'}")

    top = metrics.head(5)
    print("\nMost under-served wards (by provision gap):")
    for code, row in top.iterrows():
        name = merged.loc[merged["ward_code"] == code, "ward_name"]
        label = name.iloc[0] if len(name) and "ward_name" in merged.columns else code
        print(f"  {int(row['rank']):>2}. {label} - supply={row['supply']:.0f}, need={row['need']:.0f}, gap={row['gap']:+.4f}")


if __name__ == "__main__":
    main()
