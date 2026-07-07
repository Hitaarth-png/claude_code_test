# Replicating the interactive city map for a new city

One command builds the whole map once the data is prepared:

```
python scripts/build_city_map.py --config cities/<slug>.json
# or, if the <slug>_*.geojson files already exist in output/:
python scripts/build_city_map.py --config cities/<slug>.json --from-outputs
```

Output: `output/<slug>_idaci_interactive_map.html` — self-contained (Leaflet
vendored), plus `<slug>_ward_readiness.{csv,geojson}` and the per-layer
geojsons. Only public, aggregate datasets are used — no participant data.

## 1. Create the city config

Copy `cities/leicester.json` → `cities/<slug>.json` and set `name`, `slug`,
`lad_code`, the input file paths, and the `curated` lookups (empty `{}` to
start; fill them as the build reports unmatched sites — see §4).

## 2. Gather the data (England only — the factors are England-specific)

| Input | Source | Must contain |
|---|---|---|
| `idaci_csv` | English Indices of Deprivation, IDACI sub-domain (gov.uk), one row per LSOA pre-filtered to the LAD | LSOA code/name, ward name, IDACI score/rank/decile, IMD score/decile, `Pop_0015_2022`-style child & total population, embedded LSOA polygon (`LSOAGeo Shape` column) |
| `schools_csv` | DfE Get Information about Schools (GIAS) export for the LAD | establishment name/type/phase/status, Easting/Northing, ward, pupil counts |
| `pitches_csv` | Council playing-pitch strategy audit or Sport England Active Places | Site Name, Pitch Type, Number of Pitches, Pitch Capacity, Is 3G/AGP |
| `youth_centres_csv` | Council youth service list | name, address, postcode, age range, sessions |
| `football_providers_csv` / `social_mobility_partners_csv` (optional) | Local knowledge / council directories | organisation, type, address/base, notes |

## 3. Factors (identical to the Leicester map)

- **LSOA choropleth metrics**: IDACI decile, IMD decile, % aged 0–15, number aged 0–15 (single or bivariate blend).
- **Demand index** = mean of z-scores across wards of D1 children 0–15, D2 youth density /km², D3 population-weighted IDACI.
- **Readiness index** = mean of z-scores of S1 pitch sites and S3 schools per 1,000 children. Facilities within 400 m of a neighbouring ward count there at half weight. S2 (sports halls) and S4 (green space) are dropped until data exists and recorded in `readiness_indicators`.
- **Cold-spot quadrants**: median split of demand × readiness → Q1 priority / Q2 activate / Q3 comfortable / Q4 monitor.
- **Football provision gap** = ward share of deprivation-weighted need (children × IDACI ÷ city-mean IDACI) − share of pitch capacity; positive = under-served.

Full rationale: `METHODOLOGY.md`. Implementations: `scripts/ward_readiness.py` (indices), `scripts/idaci_interactive_map.py` (map + layers).

## 4. Per-city curation (the only manual part)

The pitch audit and youth-centre lists rarely carry coordinates. The build
prints every unmatched site; resolve them by adding entries to `curated` in
the config: `pitch_site_to_school` (site name → [GIAS school name, approx?]),
`youth_centre_postcode_coords` (postcode → [lat, lon, approx?]),
`football_provider_coords` / `_to_school`, `social_mobility_partner_coords`.
Rerun until the unmatched list is empty (or acceptably small).

## 5. Verify before sharing

- `<slug>_ward_readiness.csv`: one row per ward (Leicester has 21), z-score columns average ≈ 0, every ward has a quadrant.
- HTML: no `__PLACEHOLDER__` strings left; open in a browser — layers toggle, ward popups show the indicator table, no console errors.
- Sanity-check the top cold-spot wards against local knowledge, and re-validate source figures against official releases before external publication.

## Caveats to carry into any new city

- Supply = geolocated pitch sites only; wards with unmapped informal capacity read as under-served (caveat shown in the map legend).
- Small ward counts make z-scores sensitive; the quadrant view (median split) is the robust primary tool, per METHODOLOGY.md.
