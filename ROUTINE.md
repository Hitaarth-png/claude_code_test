# Interactive City Plan — Routine (DRAFT)

A repeatable routine for producing and refreshing the interactive city plan
(currently Wolverhampton). Follow top-to-bottom to build from scratch, or jump to
**§5 Refresh** to update one data source. The executable form of this routine is
`python scripts/run_pipeline.py`; this document is the human runbook around it.

> **Status:** DRAFT. Data governance — this routine uses only **aggregate,
> public, LSOA/ward-level** statistics and public facility registers. No
> pupil-level, personal, or safeguarding data is used or accepted.

---

## 1. Output
A single self-contained `output/city_plan.html` (interactive Leaflet map) plus
`output/city_plan_lsoa.geojson` / `city_plan_wards.geojson`. Layers: IMD & IDACI
deprivation, youth population 0–15, asset counts (schools, football
pitches/providers, youth-mobility centres), and derived analysis — infrastructure
readiness, cold-spot quadrants, coverage level, ward provision & priority scores.

## 2. One-time setup
```bash
pip install -r requirements.txt          # geopandas, folium, branca, pyyaml, openpyxl …
```
Confirm the target authority in `config.yaml` (`city.name`, `city.lad_code`,
`city.gias_la_code`).

## 3. Data inputs — obtain & place
Drop each real file at its `config.yaml` path (or its `*_raw` path where a prep
step applies), then run the routine. **Anything missing is auto-filled with clearly
labelled synthetic demo data; real files are never overwritten.**

| Layer | Real source | Where it goes | Prep step | Status |
|---|---|---|---|---|
| LSOA boundaries (2021) | From the IoD export's `geom` | `paths.boundaries` | `prepare_imd.py` | **real** |
| IMD / IDACI | Indices of Deprivation, LSOA 2021 CSV | `paths.imd_raw` | `prepare_imd.py` | **real** |
| Schools & youth-mobility centres | GIAS establishment export | `paths.gias_raw` | `prepare_gias.py` | **real** |
| Youth population 0–15 | ONS LSOA population estimates (2021 LSOAs) | `paths.youth_population` | `trim_ons_population.py` (shrink locally first) | synthetic → real when supplied |
| Football pitches / sites | Sport England **Active Places** (or OSM) | `paths.assets.football_pitches` | converter TBD | synthetic |
| Football providers / clubs | England Football / County FA directory | `paths.assets.football_providers` | converter TBD | synthetic |

Notes:
- **Big ONS file:** run `python scripts/trim_ons_population.py <ons_file.xlsx> --sheet "<sheet>"` locally to produce the small `data/youth_pop.csv`, then use/upload that.
- **Football data:** Active Places CSV (Site Name + easting/northing) or an OSM
  overpass-turbo GeoJSON export both work; a `prepare_*` converter is added when
  the file arrives (same pattern as `prepare_gias.py`).
- Asset CSVs need only a `name` column + location (`lon,lat` or `easting,northing`).

## 4. Run the routine
```bash
python scripts/run_pipeline.py           # full build; --no-sample to require real inputs only
```
Stage order (all in `scripts/`, orchestrated by `run_pipeline.py`):

1. `prepare_imd.py` — IoD → `data/imd.csv` + 2021 boundaries *(if `imd_raw` present)*
2. `prepare_gias.py` — GIAS → real `schools.csv`, `youth_mobility_centres.csv` *(if `gias_raw` present)*
3. `make_sample_data.py` — fabricate only the still-missing inputs, on real geometry
4. `ingest.py` — normalise sources → `build/lsoa_attributes.csv`
5. `geocode_assets.py` — spatial-join asset points to LSOAs → counts
6. `build_metrics.py` — derive readiness / quadrants / coverage / provision / priority
7. `build_geojson.py` — merge onto geometry → enriched GeoJSON (+ dissolved wards)
8. `build_map.py` — render `output/city_plan.html`

Open `output/city_plan.html` in a browser (needs internet for basemap tiles).

## 5. Refresh a single source
1. Replace the file at its `config.yaml` path (or re-run the relevant `prepare_*`).
2. `python scripts/run_pipeline.py` — real files are kept; only missing layers are fabricated.
3. Spot-check the affected layer in the map and the summary/priority panel.

Suggested cadence: IoD on each release; GIAS termly; ONS population annually;
football data on the local Playing Pitch Strategy cycle.

## 6. Adapt to another authority
Edit `config.yaml` → `city.name`, `city.lad_code` (ONS), `city.gias_la_code`
(GIAS), and supply that authority's data files. No code changes needed — column
names are auto-detected and boundaries come from the IoD export.

## 7. Verification checklist
- [ ] Pipeline completes with no errors; stage logs show expected row counts.
- [ ] Map renders the real borough outline; every layer toggles with its own legend.
- [ ] Asset pins show real names; popups show IMD/IDACI **deciles** (1 = most deprived).
- [ ] Priority tracks deprivation (most-deprived LSOAs rank highest).
- [ ] `data/raw` and real asset CSVs are **not** overwritten on re-run.
- [ ] README "layer status" reflects which layers are real vs synthetic.
