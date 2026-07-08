"""Stage 3 — derive the analysis layers from the ingested attributes + asset counts.

Per-LSOA outputs: weighted provision, infrastructure readiness, need score,
coverage level, cold-spot quadrant, priority score.
Per-ward roll-up: provision score, mean deprivation, priority, cold-spot count.

Writes <work_dir>/lsoa_metrics.csv and <work_dir>/ward_metrics.csv.
"""
from __future__ import annotations

import pandas as pd

from common import load_config, minmax, resolve

COVERAGE_LABELS = ["None/Very low", "Low", "Moderate", "Good", "High"]


def main():
    cfg = load_config()
    work = resolve(cfg["work_dir"])
    aw = cfg["asset_weights"]
    pw = cfg["priority_weights"]
    n_bands = cfg.get("coverage_bands", 5)

    attrs = pd.read_csv(work / "lsoa_attributes.csv")
    counts = pd.read_csv(work / "asset_counts_lsoa.csv")
    df = attrs.merge(counts, on="lsoa_code", how="left")

    asset_cols = list(aw.keys())
    for c in asset_cols:
        df[c] = df.get(c, 0).fillna(0)
        # Accessibility score per type; falls back to the raw count if absent.
        acc = f"{c}_access"
        df[acc] = df[acc].fillna(0) if acc in df else df[c]

    # Provision uses the accessibility score (reach), relative to the youth
    # cohort it should serve; assets_total keeps the raw in-LSOA counts.
    df["weighted_assets"] = sum(df[f"{c}_access"] * aw[c] for c in asset_cols)
    df["assets_total"] = df[asset_cols].sum(axis=1)
    youth = df["youth_population"].clip(lower=1)
    df["provision_per_1000_youth"] = (df["weighted_assets"] / youth * 1000).round(2)

    # Infrastructure readiness: provision scaled to 0-100.
    df["infrastructure_readiness"] = (minmax(df["provision_per_1000_youth"]) * 100).round(1)

    # Need: deprivation (IDACI, IMD) blended with youth cohort size.
    need = (0.5 * minmax(df["idaci_score"])
            + 0.25 * minmax(df.get("imd_score"))
            + 0.25 * minmax(df["youth_population"]))
    df["need_score"] = (need * 100).round(1)

    # Coverage level: banded provision.
    prov_rank = df["provision_per_1000_youth"].rank(method="first")
    bands = pd.qcut(prov_rank, min(n_bands, prov_rank.nunique()), labels=False, duplicates="drop")
    labels = COVERAGE_LABELS[:bands.max() + 1] if bands.notna().any() else COVERAGE_LABELS
    df["coverage_level"] = pd.Series(bands).map(dict(enumerate(labels)))

    # Cold-spot quadrant: high need x low provision (median splits).
    hi_need = df["need_score"] >= df["need_score"].median()
    hi_prov = df["provision_per_1000_youth"] >= df["provision_per_1000_youth"].median()
    quad = pd.Series("Low demand", index=df.index)
    quad[hi_need & ~hi_prov] = "Cold spot (priority)"
    quad[hi_need & hi_prov] = "High need, well served"
    quad[~hi_need & hi_prov] = "Over-provided"
    df["cold_spot_quadrant"] = quad

    # Priority score: weighted blend, deprivation + youth + scarcity of provision.
    df["priority_score"] = (100 * (
        pw["idaci"] * minmax(df["idaci_score"])
        + pw["imd"] * minmax(df.get("imd_score"))
        + pw["youth_pop"] * minmax(df["youth_population"])
        + pw["inverse_provision"] * (1 - minmax(df["provision_per_1000_youth"]))
    )).round(1)

    df.to_csv(work / "lsoa_metrics.csv", index=False)

    # Ward roll-up.
    g = df.groupby("ward_name")
    ward = pd.DataFrame({
        "youth_population": g["youth_population"].sum(),
        "weighted_assets": g["weighted_assets"].sum(),
        "assets_total": g["assets_total"].sum(),
        "idaci_score": g["idaci_score"].mean(),
        "imd_score": g["imd_score"].mean() if "imd_score" in df else g["idaci_score"].mean(),
        "priority_score": g["priority_score"].mean(),
        "lsoa_count": g.size(),
        "cold_spots": g["cold_spot_quadrant"].apply(lambda s: (s == "Cold spot (priority)").sum()),
    }).reset_index()
    ward["ward_provision_score"] = (
        ward["weighted_assets"] / ward["youth_population"].clip(lower=1) * 1000).round(2)
    ward = ward.round(2)
    ward.to_csv(work / "ward_metrics.csv", index=False)

    print(f"Wrote lsoa_metrics.csv ({len(df)} LSOAs) and ward_metrics.csv ({len(ward)} wards).")
    print(f"Cold spots: {(df['cold_spot_quadrant'] == 'Cold spot (priority)').sum()} LSOAs.")


if __name__ == "__main__":
    main()
