# Leicester ward-level infrastructure readiness & cold-spot quadrant methodology

**Purpose.** Rank Leicester's 21 electoral wards by (a) how ready their physical
infrastructure is to host youth football provision, and (b) how urgent the need
for provision is — then combine the two into a four-quadrant "cold-spot"
classification that tells an expansion team where to act and how.

This extends the existing LSOA-level Indices of Deprivation map in
`output/leicester_imd_decile_map_2025.png` from a *need-only* view to a
*need-vs-supply* view at ward level.

---

## 1. Unit of analysis

Leicester City (unitary authority, LAD `E06000016`) has **21 electoral wards**
(boundaries in force since May 2015). Wards are the unit BFF plans provision
around (recognisable neighbourhoods, council engagement happens at ward level).

Most need-side statistics are published at **LSOA** level (Lower layer Super
Output Areas, ~1,500 residents each; Leicester has 192 on 2011 boundaries).
LSOA values are aggregated to wards:

- **scores / rates** → population-weighted mean across the ward's LSOAs;
- **counts** (e.g. children) → sum;
- LSOA→ward assignment uses the ONS best-fit lookup where available, otherwise
  each LSOA is assigned to the ward containing its centroid (both methods are
  standard ONS practice for non-nesting geographies).

## 2. Demand (need) index — "how much provision is needed here?"

Per-ward indicators, each standardised to a z-score across the 21 wards, then
averaged with equal weights:

| Code | Indicator | Rationale |
|------|-----------|-----------|
| D1 | Youth population (children in the ward) | Scale of the potential participant base |
| D2 | Youth density (children per km²) | Intensity — dense wards support walk-to sessions |
| D3 | Child income deprivation (IDACI score, population-weighted; IMD income-domain fallback) | BFF prioritises families who cannot afford commercial clubs |

`Demand = mean(z(D1), z(D2), z(D3))`

## 3. Infrastructure readiness index — "can we deliver here tomorrow?"

Per-ward supply-side indicators (facility points assigned to wards spatially;
facilities within a short buffer of the ward boundary count at half weight,
because catchments ignore administrative lines):

| Code | Indicator | Rationale |
|------|-----------|-----------|
| S1 | Football pitches per 1,000 children | Core delivery surface |
| S2 | Sports halls / leisure centres per 10,000 residents | All-weather indoor capacity |
| S3 | Schools per 1,000 children | Primary venue-partnership route for BFF |
| S4 | Public green space share of ward area | Informal/pop-up session capacity |

`Readiness = mean(z(S1), z(S2), z(S3), z(S4))`

Indicators are dropped from the mean (not imputed) for any ward where the
underlying source has no coverage; the output CSV records which indicators fed
each ward's score.

## 4. Cold-spot quadrant classification

Wards are plotted on a Demand × Readiness plane and split at the **median** of
each axis (medians rather than means so the split is robust to outliers and
always yields a balanced, comparative view of the city):

| Quadrant | Demand | Readiness | Label | Action implication |
|----------|--------|-----------|-------|--------------------|
| Q1 | high | low | **Cold spot — priority** | Highest expansion priority; needs venue investment/partnerships (school halls, pop-ups) before programmes can scale |
| Q2 | high | high | **Activate existing assets** | Fastest wins — demand and infrastructure both present; launch programmes on existing pitches/halls |
| Q3 | low | high | **Comfortable / saturated** | Low priority; existing capacity likely serves current need |
| Q4 | low | low | **Monitor** | Low current need; revisit as demographics shift |

A supplementary spatial statistic (Getis–Ord Gi* on the readiness-per-child
ratio, queen contiguity) is reported where feasible to flag statistically
significant clusters of under-provision, with the caveat that n=21 wards is a
small sample for inference — the quadrant classification is the primary tool.

## 5. Outputs

- `output/leicester_ward_readiness.csv` — full indicator table (raw values,
  z-scores, indices, quadrant)
- `output/leicester_ward_readiness_map.png` — static multi-panel map (demand,
  readiness, quadrants, scatter)
- `output/leicester_ward_readiness_map.html` — interactive map carrying
  forward the existing LSOA deprivation layer with the new ward-level
  readiness/quadrant layers and facility points on top

## 6. Data sources & caveats

All sources are open government data (OGL) obtained via public mirrors —
this environment's network policy blocks direct access to ons.gov.uk /
gov.uk / nomisweb, so datasets were fetched from published GitHub mirrors of
the official releases. Exact provenance (repo, path, vintage) is recorded in
`data/SOURCES.md`. Figures should be re-validated against the official
releases before external publication.

<!-- SOURCES-FINALISED-AFTER-FETCH -->
