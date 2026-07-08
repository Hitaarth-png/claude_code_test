# Interactive City Plan — Wolverhampton

An interactive Leaflet map of the City of Wolverhampton (LAD `E08000031`) that
overlays youth-provision need and supply at LSOA and ward level, with derived
analysis layers for prioritising investment.

All inputs are **aggregate, public, LSOA/ward-level statistics** — no personal,
pupil-level, or safeguarding data.

## Layers produced

**Base factors**
- IDACI score & IMD score/decile (deprivation)
- Youth population (0–15)
- LSOA code / ward name
- Asset counts: schools, football pitch sites, football providers, youth
  mobility centres

**Derived analysis layers**
- **Infrastructure readiness** — weighted asset density vs the youth cohort (0–100)
- **Cold-spot quadrants** — high-need × low-provision 2×2 classification
- **Coverage level** — provision banded into 5 levels
- **Ward provision score** — weighted assets per 1,000 youth, rolled up to ward
  (provision uses an **accessibility** measure — a distance-decayed count of
  assets within `provision.radius_m` of each LSOA, so assets just over a boundary
  still count; set `provision.method: containment` in `config.yaml` for the old
  strictly-inside behaviour)
- **Priority score** — weighted blend of IDACI, IMD, youth population and
  provision scarcity (weights in `config.yaml`)

## Quick start

```bash
pip install -r requirements.txt
python scripts/run_pipeline.py --city wolverhampton   # one city -> output/wolverhampton/city_plan.html
python scripts/run_all.py                             # every city in cities.yaml + output/index.html
```

Cities are listed in **`cities.yaml`** (name + ONS `lad_code` + DfE
`gias_la_code`); `config.yaml` holds only city-agnostic settings. With no input
files present, the pipeline fetches **real boundary geometry** for the city (so
the map has the true shape) and layers **synthetic attributes and asset points**
on top so it runs end-to-end. Fabricated attributes are for verifying the
pipeline only — not for analysis. Use `--no-sample` to require real inputs.

Open `output/<city>/city_plan.html` in a browser (needs internet for the basemap
tiles). Toggle layers via the control top-right; hover an LSOA for its stats.

## Using real data

Place the shared national files below under `data/raw/` (paths in `config.yaml`
→ `raw.*`); each is filtered to a city by its codes. Column names are
auto-detected, so raw government headers work unchanged. Real files are **never
overwritten** — `make_sample_data.py` fabricates only the inputs still missing.

| Config path | Source |
|---|---|
| `paths.imd_raw` | Indices of Deprivation export (LSOA 2021, incl. `geom`) — supplies IMD/IDACI **and** boundaries |
| `paths.youth_population` | ONS mid-year population estimates — ages 0–15 per LSOA |
| `paths.assets.*` | Point data (lon/lat or easting/northing) for football pitches/providers (FA / Active Places) |

`paths.boundaries` is auto-generated from `imd_raw` (2021 LSOA geometry); the
`lsoa_ward_lookup` is derived by spatially assigning LSOAs to ward polygons.

### Youth population (0–15) — trimming the big ONS file

The full ONS LSOA population file is large (single year of age × ~35k LSOAs).
Shrink it locally to the tiny input the pipeline needs:

```bash
python scripts/trim_ons_population.py <ons_file.xlsx> --sheet "Mid-2022 Persons"
```

It auto-detects the header row, the LSOA code column and the age columns, sums
ages 0–15, keeps only this city's LSOAs, and writes
`data/youth_pop.csv` (~161 rows). Adjust the cohort with `--min-age/--max-age`.

### Indices of Deprivation (IMD/IDACI)

Drop a raw IoD LSOA-2021 CSV at `paths.imd_raw`. `prepare_imd.py` filters it to
`city.lad_code`, writes the IMD/IDACI **deciles + ranks** (`paths.imd`) and the
**2021 LSOA boundaries** (from the embedded `geom`), keeping the deprivation and
geometry on the same LSOA vintage. `ingest.py` derives a 0–1 deprivation
magnitude from the national rank (or decile) where no raw score is provided.

### Schools & youth-mobility centres — GIAS

Drop a raw **GIAS** export (Get Information About Schools, gov.uk) at `raw.gias`.
`prepare_gias.py` keeps open establishments whose GIAS LA code matches the city's
`gias_la_code` (from `cities.yaml`; Wolverhampton = `336`), converts
Easting/Northing to lon/lat, and writes real `schools.csv` and
`youth_mobility_centres.csv` (children's centres). Only establishment
**name + location + type** are used — no pupil, FSM, or staff fields.

**Current layer status:** boundaries (LSOA 2021), IMD/IDACI deprivation, schools
and youth-mobility centres are **real**; youth population (0–15) and football
pitches/providers remain **synthetic** demo data on real geometry until their
real sources are supplied.

## Pipeline stages (`scripts/`)

| Stage | Script | Output |
|---|---|---|
| 0a | `prepare_imd.py` | real IMD/IDACI (`data/imd.csv`) + 2021 boundaries |
| 0b | `prepare_gias.py` | real `data/assets/schools.csv`, `…/youth_mobility_centres.csv` |
| 0c | `make_sample_data.py` | synthetic fill for missing inputs, on real geometry (`data/`) |
| 1 | `ingest.py` | `build/lsoa_attributes.csv` |
| 2 | `geocode_assets.py` | `build/asset_counts_lsoa.csv` |
| 3 | `build_metrics.py` | `build/lsoa_metrics.csv`, `build/ward_metrics.csv` |
| 4 | `build_geojson.py` | `output/city_plan_lsoa.geojson`, `…_wards.geojson` |
| 5 | `build_map.py` | `output/city_plan.html` |

Paths above are namespaced per city (`data/<slug>/…`, `output/<slug>/…`).
`run_pipeline.py --city <slug>` runs one city; `run_all.py` builds every city in
`cities.yaml` plus `output/index.html`. `common.py` holds the city-aware config
loader and column-detection helpers.

## Adding a city

Add a row to `cities.yaml` — `{ name, lad_code (ONS), gias_la_code (DfE) }` — and
run `run_pipeline.py --city <slug>`. No code changes: shared national files are
filtered by these codes, boundaries come from the IoD `geom`, and any layer with
no data for that city is fabricated (clearly labelled) until real data arrives.
See **ROUTINE.md** for the full runbook.
