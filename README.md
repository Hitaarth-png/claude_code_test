# Bloomsbury Football Foundation — geospatial analysis

Small geopandas scripts that combine public, aggregate open data to map where football
opportunity is most needed. **No participant, safeguarding, financial or other
restricted data is used or required** — every input below is publicly published.

## Scripts

### `scripts/ward_football_provision.py` — deprivation-weighted football provision by ward (Manchester)

Answers: *how much football capacity does each ward have relative to how much it needs?*

**Method** (per ward *w*):

| Quantity | Formula | Meaning |
|----------|---------|---------|
| `supply_w` | Σ `pitch_count × capacity_factor[pitch_type]` | participant places available |
| `weight_w` | ward IDACI ÷ mean ward IDACI | deprivation multiplier (~1 = average) |
| `need_w` | `child_pop_w × weight_w` | deprivation-weighted demand |
| `provision_w` | `supply_w ÷ need_w` | places per weighted child |
| `gap_w` | `need_w/Σneed − supply_w/Σsupply` | **+ve = under-served** (more need than supply) |

Capacity factors (`CAPACITY_FACTORS` in the script) are tunable modelling assumptions,
not facts — adjust them in one place if you have Sport England / BFF capacity guidance.

**Run:**

```bash
pip install -r requirements.txt

python scripts/ward_football_provision.py \
  --facilities        active_places_manchester.csv \
  --boundaries        wards.geojson \
  --population        ward_population.csv \
  --idaci             imd2019_lsoa.csv \
  --lsoa-ward-lookup  lsoa_to_ward.csv \
  --outdir            output
# add --pop-column "<name>" if your population file already has a pre-summed child column
```

**Outputs** (`output/`): `manchester_ward_provision.png` (choropleth of the provision
gap), `manchester_ward_provision.csv` (per-ward supply/need/provision/gap/rank), and
`manchester_ward_provision.geojson`.

### `scripts/idaci_leicester_map.py`

Renders an IDACI (Income Deprivation Affecting Children Index) choropleth by LSOA for
the City of Leicester. See the docstring at the top of the file.

## Where to get the data

All datasets are free downloads. Codes below are for Manchester (metropolitan district,
LAD `E08000003`; its wards use `E05…` codes).

| # | Dataset | Source | Notes |
|---|---------|--------|-------|
| 1 | **Ward boundaries** (GeoJSON/Shapefile, `WD__CD`) | [ONS Open Geography Portal](https://geoportal.statistics.gov.uk/) — "Wards (Dec 2023/24) Boundaries" | Can be pre-filtered to Manchester, or the script filters by LAD code/name/`E05` prefix |
| 2 | **Football facilities / pitches** | [Sport England Active Places](https://www.activeplacespower.com/) open data / "Active Places Power" export | Keep the facility-type + latitude/longitude columns; grass pitches, 3G/AGP, MUGA, small-sided |
| 3 | **Ward child population** | [ONS / NOMIS](https://www.nomisweb.co.uk/) — Census 2021 age-by-ward, or ward population estimates | Single-year-of-age columns are summed for ages 3–18; or pass `--pop-column` |
| 4 | **Deprivation — IDACI (or IMD)** | [English Indices of Deprivation 2019 (gov.uk)](https://www.gov.uk/government/statistics/english-indices-of-deprivation-2019) | Per-LSOA scores; IDACI is child-specific |
| 5 | **LSOA → ward best-fit lookup** | ONS Open Geography Portal — "LSOA to Ward" lookup | Used to aggregate deprivation (#4) up to wards |
| 6 | **Ward → LAD lookup** | Usually already columns in the boundary file (#1); otherwise the ONS "Ward to LAD" lookup | Used to isolate Manchester |

### Notes / assumptions
- **Child age band** snaps to whatever ages the population table publishes (e.g. 0–15 or
  5–17) as the closest proxy for BFF's 3–18 range.
- **Deprivation weight** uses **IDACI** by default; the score loader also accepts an IMD
  score column if you prefer overall deprivation.
- The provision `gap` sums to ≈ 0 across wards by construction; rank 1 = most under-served.
