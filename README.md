# Leicester IDACI & infrastructure readiness maps

Maps of child income deprivation (IDACI) and football/sports "infrastructure
readiness" per LSOA in the City of Leicester, for programme planning.

## Scripts

- `scripts/idaci_leicester_map.py` — static PNG choropleth of IDACI scores.
  Requires `--idaci` (IoD 2019 File 7 CSV/Excel) and `--boundaries` (LSOA
  GeoJSON/Shapefile) supplied manually.
- `scripts/leicester_readiness_map.py` — interactive HTML map
  (`output/leicester_idaci_readiness_map.html`) with toggleable IDACI and
  readiness choropleths plus facility points, and a per-LSOA CSV
  (`output/leicester_readiness_lsoa.csv`). Downloads and caches its own data
  in `data/` on first run; later runs work offline. Local files can be
  supplied instead via `--idaci`, `--boundaries`, `--facilities`.

```bash
pip install -r requirements.txt
python scripts/leicester_readiness_map.py
```

## Readiness score (0–1)

For each LSOA, facilities are counted within a catchment buffer of the LSOA
polygon: pitches and parks within 800 m, sports centres/halls within 1200 m.
Each count is scaled against the city-wide 90th percentile (capped at 1),
then combined: **0.5 × pitches + 0.3 × sports centres + 0.2 × parks**.
Buffers and weights are assumptions — tune with `--pitch-buffer`,
`--pitch-weight`, etc.

Caveats: OpenStreetMap facility coverage is incomplete and uneven; the score
measures proximity/quantity, not quality, availability or cost of facilities.

## Data sources & licences

- IDACI: English Indices of Deprivation 2019, File 7 (MHCLG/DLUHC, gov.uk) — OGL v3.
- LSOA 2011 boundaries: ONS Open Geography Portal — OGL v3.
- Facilities: © OpenStreetMap contributors, via Overpass API — ODbL.

## Note for Claude Code remote sessions

The default remote environment network policy blocks
`assets.publishing.service.gov.uk`, `services1.arcgis.com` and
`overpass-api.de`, so the first (downloading) run must happen locally, or
those hosts must be allowed in the environment's network settings. Once the
`data/` cache is committed, the map rebuilds anywhere.
