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
- **Priority score** — weighted blend of IDACI, IMD, youth population and
  provision scarcity (weights in `config.yaml`)

## Quick start

```bash
pip install -r requirements.txt
python scripts/run_pipeline.py           # builds output/city_plan.html
```

With no input files present, the pipeline fetches the **real ONS LSOA/ward
boundary geometry** for the configured authority (so the map has the true city
shape) and layers **synthetic attributes and asset points** on top so it runs
end-to-end. The fabricated attributes are for verifying the pipeline only — not
for analysis. Use `--no-sample` to require real inputs instead.

Open `output/city_plan.html` in a browser (needs internet for the basemap
tiles). Toggle layers via the control top-right; hover an LSOA for its stats.

## Using real data

Download the files below and place them at the paths in `config.yaml`
(`data/…`), then re-run the pipeline. Column names are auto-detected, so the
raw government headers work unchanged. Real files are **never overwritten** —
`make_sample_data.py` fabricates only the inputs that are still missing.

| Config path | Source |
|---|---|
| `paths.boundaries` | ONS Open Geography Portal — LSOA (2011) boundaries, filtered to Wolverhampton |
| `paths.imd` | English Indices of Deprivation 2019 (gov.uk) — IMD + IDACI scores/deciles per LSOA |
| `paths.youth_population` | ONS mid-year population estimates — ages 0–15 per LSOA |
| `paths.lsoa_ward_lookup` | ONS LSOA → Ward best-fit lookup |
| `paths.assets.*` | Point data (lon/lat or easting/northing) for football pitches/providers (FA / Active Places) |

### Schools & youth-mobility centres — GIAS

Drop a raw **GIAS** export (Get Information About Schools, gov.uk) at
`paths.gias_raw`. `prepare_gias.py` keeps open establishments in
`city.gias_la_code` (Wolverhampton = `336`), converts Easting/Northing to
lon/lat, and writes real `schools.csv` (114 schools) and
`youth_mobility_centres.csv` (16 children's centres). Only establishment
**name + location + type** are used — no pupil, FSM, or staff fields.

**Current layer status:** schools and youth-mobility centres are **real**
(GIAS); IMD/IDACI, youth population, and football pitches/providers are
**synthetic** demo data on real geometry until their real sources are supplied.

## Pipeline stages (`scripts/`)

| Stage | Script | Output |
|---|---|---|
| 0a | `prepare_gias.py` | real `data/assets/schools.csv`, `…/youth_mobility_centres.csv` |
| 0b | `make_sample_data.py` | real ONS boundaries + synthetic fill for missing inputs (`data/`) |
| 1 | `ingest.py` | `build/lsoa_attributes.csv` |
| 2 | `geocode_assets.py` | `build/asset_counts_lsoa.csv` |
| 3 | `build_metrics.py` | `build/lsoa_metrics.csv`, `build/ward_metrics.csv` |
| 4 | `build_geojson.py` | `output/city_plan_lsoa.geojson`, `…_wards.geojson` |
| 5 | `build_map.py` | `output/city_plan.html` |

`run_pipeline.py` runs stages 1–5 (and 0 if inputs are missing). `common.py`
holds shared config/column-detection helpers. Targeting another authority is a
matter of editing `city` in `config.yaml` and supplying its data files.
