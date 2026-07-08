# City deprivation maps (IDACI by LSOA)

Choropleth maps of the **Income Deprivation Affecting Children Index (IDACI)**
for every Lower layer Super Output Area (LSOA) in a city. IDACI measures the
proportion of children aged 0–15 living in income-deprived families in each
small area — a standard, published open-data indicator (English Indices of
Deprivation, gov.uk) used for siting and prioritising services for children.

| City | Script | Output |
|------|--------|--------|
| Leicester (`E06000016`) | `scripts/idaci_leicester_map.py` | static PNG choropleth |
| Nottingham (`E06000018`) | `scripts/idaci_nottingham_interactive_map.py` | **interactive** Leaflet/Folium HTML |

The Nottingham map follows the same workflow as the Leicester one
(ingest IDACI scores → ingest LSOA boundaries → filter to the city → join →
render) but produces a self-contained interactive HTML map: hover any LSOA to
see its code, name, IDACI score, and — if the source file provides it — its
national IDACI decile.

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Data inputs (download these — they are not committed)

Both scripts read two public files. They are not committed because they are
large and freely published; download the current release and pass the paths in.

1. **IDACI scores per LSOA** — English Indices of Deprivation, gov.uk
   (MHCLG). Search *"English indices of deprivation"* on gov.uk and download
   the **"File 5: scores"** spreadsheet from the latest release
   (Indices of Deprivation **2025**, or **2019** if you need to match older
   boundaries). It contains an *LSOA code* column and an
   *"Income Deprivation Affecting Children Index (IDACI) Score"* column; the
   scripts auto-detect them, and pick up an IDACI decile column if present.

2. **LSOA boundaries** — ONS Open Geography Portal
   (`https://geoportal.statistics.gov.uk`). Download the
   *"Lower layer Super Output Areas … Boundaries"* GeoJSON or Shapefile whose
   **vintage matches the IoD release** (2011 LSOAs for IoD2019, 2021 LSOAs for
   IoD2025). The file must contain an LSOA code property (`LSOA11CD` /
   `LSOA21CD`). A national file works — the script filters to the city itself.

## Run

```bash
# Interactive Nottingham map
python3 scripts/idaci_nottingham_interactive_map.py \
    --idaci path/to/IoD_File5_Scores.xlsx \
    --boundaries path/to/LSOA_boundaries.geojson \
    --outdir output

# Static Leicester map
python3 scripts/idaci_leicester_map.py \
    --idaci path/to/IoD_File5_Scores.xlsx \
    --boundaries path/to/LSOA_boundaries.geojson \
    --outdir output
```

The Nottingham script writes to `--outdir`:

- `nottingham_idaci_interactive_map.html` — open in a browser
- `nottingham_idaci_lsoa.csv` — the joined attribute table
- `nottingham_idaci_lsoa.geojson` — the joined geometries (EPSG:27700)

LSOAs in the boundary file with no matching IDACI score are drawn in grey and
reported on stderr.
