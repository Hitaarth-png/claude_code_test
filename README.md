# Small-area statistics scripts

Reproducible Python scripts for building small-area (LSOA / ward) statistics for
English local authorities from **official, publicly available** open data. The
scripts work only with aggregate area-level counts — no personal or restricted
data is used or required.

```
pip install -r requirements.txt
```

## Manchester youth population (age 0–15) by LSOA and ward

`scripts/manchester_youth_population.py` gathers the population aged **0–15** for
every LSOA in the City of Manchester (LAD `E08000003`), tags each LSOA with the
electoral ward it falls in, and rolls the figures up to ward level.

### Data you need to download

Both sources are free and open. Download them once and pass their paths to the
script.

1. **Age by single year of age — Census 2021 (TS007), at LSOA level.**
   Nomis: <https://www.nomisweb.co.uk/datasets/c2021ts007>
   - Easiest: the England & Wales **bulk** CSV from
     <https://www.nomisweb.co.uk/sources/census_2021_bulk> (download “TS007 – Age
     by single year of age”). This is a *wide* table with one column per age
     (`Aged under 1 year`, `Aged 1 year`, …).
   - Or build a custom download on the dataset page filtered to Manchester LSOAs.
     The API `.data.csv` *long* form (one row per age) is also accepted.

   > Single year of age is required to isolate ages 0–15 cleanly. The five-year
   > band product (TS007A) cannot, because age 15 sits inside the 15–19 band.

2. **LSOA (2021) → Electoral Ward → LAD best-fit lookup (England & Wales), CSV.**
   ONS Open Geography Portal, most recent edition, e.g.:
   <https://geoportal.statistics.gov.uk/datasets/ons::lsoa-2021-to-electoral-ward-2024-to-lad-2024-best-fit-lookup-in-ew/about>
   (Download → CSV). Any recent year (2022–2025) works; the script auto-detects
   the `LSOA21CD` / `WDxxCD` / `WDxxNM` / `LADxxCD` columns.

3. *(Optional, for a map)* **LSOA 2021 boundaries** (GeoJSON/Shapefile), ONS Open
   Geography Portal.

### Run

```bash
python scripts/manchester_youth_population.py \
    --age-data    TS007_age_single_year.csv \
    --ward-lookup LSOA21_to_Ward_to_LAD.csv \
    --outdir      output
```

Add `--boundaries LSOA_2021_boundaries.geojson` to also render a choropleth map.

Useful flags: `--lad-code` (default `E08000003`, so the same script works for any
LAD) and `--max-age` (default `15`).

### Outputs (`output/`)

| File | Contents |
| --- | --- |
| `manchester_youth_pop_0_15_by_lsoa.csv` | One row per Manchester LSOA: `lsoa_code`, `ward_code`, `lsoa_name`, `ward_name`, `pop_youth` (0–15), `pop_total`, `pct_youth`. |
| `manchester_youth_pop_0_15_by_ward.csv` | Aggregated to ward: `ward_code`, `ward_name`, `n_lsoas`, `pop_youth`, `pop_total`, `pct_youth`. |
| `manchester_youth_pop_0_15_map.png` / `.geojson` | Only with `--boundaries`. |

### Notes

- **Best-fit caveat.** LSOAs do not nest perfectly inside electoral wards, so ONS
  publishes a *best-fit* LSOA→ward lookup. Ward totals here are the sum of their
  best-fit LSOAs and will differ slightly from ward figures built from output
  areas. This is the standard trade-off for LSOA-level ward reporting.
- **Vintage.** Population is Census 2021 (Census Day 21 March 2021). Choose a
  ward lookup vintage appropriate to the ward boundaries you want to report on.

## Leicester IDACI map

`scripts/idaci_leicester_map.py` renders a choropleth of the Income Deprivation
Affecting Children Index (IDACI, English Indices of Deprivation 2019) for every
LSOA in the City of Leicester. It needs an IDACI-by-LSOA file (gov.uk IoD 2019)
and an LSOA boundaries file (ONS). See the script header for arguments.
