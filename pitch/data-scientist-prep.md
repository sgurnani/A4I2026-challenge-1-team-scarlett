# Data scientist prep: round 2 pitch (8 min, judges)

Every number below was checked against BigQuery or the repo on 2026-11-19.

## 0. Fix on the Sources slide before recording

- The bullet **"Tested on unseen areas:"** ends with a colon and no text. Make it:
  "Tested on unseen areas: whole 2.7 km blocks held out of training (442 cells)".
- Optional: "Data corrected: 26% of grid cells re-assigned" → "26% of grid cells moved to their correct 2024
  Census tract" (otherwise a judge asks "re-assigned to what?").

## 0b. "Why Chicago?"

Checked 2026-11-19 against the challenge README and the original sources. Corrections from the first draft are
marked ⚠.

**Data reasons (from the challenge guide)**

- **Abundant street-level data:** Chicago's Heat Watch campaign has ~49,000 readings (48,855 in our table).
- **Second-largest of the 8 recommended cities** (68 are available in total; the guide recommends 8 as tested
  end to end). Houston, 147,000, Chicago 49,000, Seattle 35,000, Dallas (the notebook default) 19,000.
- **Volume without the penalty:** the guide calls Houston "the largest campaign by far, and the slowest to load."
  Chicago gives a third of Houston's volume, enough to hold out 442 cells for testing and still train on 1,207.

**Why the problem matters there**

- **Warming:** NOAA records for the Chicago Midway Airport 3 SW station show the annual mean temperature up
  **2.12 °C (about 3.8 °F)** from 1942–1951 to 2016–2025. Source: ClimateObservatory.org's analysis of NOAA
  station data (climateobservatory.org/chicago-us). Say "about 4 degrees Fahrenheit" to a US audience.
- **Intense urban heat island** ⚠ use the 2024 report consistently: in Climate Central's *Urban Heat Hot Spots
  in 65 Cities* (2024), Chicago's per-capita average UHI index is **8.7 °F, 4th highest of 65 cities** (behind
  New York, San Francisco and Newark). (The 8.3 °F figure is from the 2023 report of 44 cities, where Chicago
  tied Miami for 3rd; don't mix the two.)
- **High exposure:** same 2024 report: **1.7 million residents (62%)** live where the UHI index is 8 °F or more.
- **Regional vulnerability:** CMAP (Oct 2026): **44% of northeastern Illinois** lives in census tracts with a
  Heat Vulnerability Index score of 5.7/10 or above.
- **Where vulnerability comes from** ⚠ CMAP splits this by county: in **Cook County**, extensive impervious
  surfaces intensify the heat island; **DuPage, Kane, Kendall and McHenry** have more residents 65+ and higher
  rates of diabetes, asthma and coronary disease. Don't attribute the health and age points to Cook.
- **Future warming** (verified): "Chicago is projected to warm by 9.1 °F, with summers more like Montgomery,
  Ala." by 2100. Source: Climate Central, *Shifting U.S. Cities* (July 13, 2022), high-emissions scenario
  SSP3-7.0 (updated in *Shifting Cities: Mapping Future Climates*, July 2025). For comparison in the same
  analysis: Houston +6.4 °F (like Lahore, Pakistan), New York +7.6 °F (like Columbia, S.C.), so Chicago's
  projected warming is the largest of the three. Always say "if emissions stay high"; a judge may ask.
- **And the history:** the 1995 heat wave (the deck's 6.7× stat), which is why Chicago is the textbook case for
  "help has to come to the people who can't leave home."

**Say it in 20 seconds**

> We chose Chicago for the data and for the stakes. It has the second-largest street-level heat campaign in
> the challenge, about 49,000 readings, without Houston's load time. And it needs this: it's warmed about
> 4 °F since the 1940s, it has the 4th most intense urban heat island of 65 US cities, and 1.7 million people,
> 62% of the city, live in its hottest zones. If emissions stay high, Climate Central projects Chicago summers
> will feel like Montgomery, Alabama's by 2100.

**If a judge pushes:** "You excluded CMAP data but cite CMAP?" We excluded CMAP's *canopy dataset* from the
model because its reuse license wasn't clear; quoting a published finding with attribution is different.

## 1. Data slide script (~90 seconds, ~240 words)

> The challenge gave us three tables: **NOAA Heat Watch**, 48,855 street-level air readings from one hot
> afternoon; seven years of **weather-station** highs; and **Census** demographics. That's enough to see that
> heat varies block by block, but not enough to send a truck somewhere.
>
> So we brought in everything marked with a star. **Satellite surface heat** from Landsat, measured across the
> whole city at once on 14 clear summer days. **Pavement and tree canopy** from the national land-cover map. The
> **2024 Census**, which moved a quarter of our grid cells to the correct tract. And the **City's own records**,
> 287 cooling sites, 134 of them indoor, reconciled across sources. We checked every license, dropped two
> sources we couldn't legally reuse, and **race is never loaded**.
>
> On the right is the model. Our first version used physical features, like distance to the lake, density, and
> building age, and on neighborhoods it had never seen it explained only **26%** of the variation. A simple
> map-position trend did better: **50%**. That told us the model was missing the city-wide pattern.
>
> So **before training anything new, we set a bar**: beat location alone by at least 0.02. The new model,
> location plus satellite heat plus land cover, reached **0.59**, a typical error of **one and a half degrees**
> on blocks it never saw. And when we audited where it sends help, the top sites land in the most vulnerable
> tracts at **4.3 times** the city rate.

Shorter fallback (45 s): first paragraph, then "We added satellite heat, land cover, the 2024 Census and the
City's cooling sites, licenses checked, race never loaded," then the last paragraph.

## 2. "What data did you collect outside the provided notebook?"

The notebook loads three tables: `heat_observations` (Heat Watch), `station_daily_temps` (GHCN-Daily),
`tract_demographics` (ACS). Everything else we acquired:

| Source | What it gave us | How |
|---|---|---|
| **Landsat 8/9** (USGS), summers 2019–2025, 14 clear days | Surface temperature for every cell, citywide, at one moment | Earth Engine inside BigQuery (`ST_REGIONSTATS`), cloud pixels masked |
| **NLCD 2021** (USGS/USFS) | Impervious surface % and tree canopy % within 300 m | Same, Earth Engine in BigQuery |
| **ACS 2024 5-year + 2024 tract boundaries** | Current 65+, no-car, poverty; correct tract per cell (1,305 of 5,053 cells changed tract) | BigQuery public data + Census TIGER |
| **City of Chicago open data** | 287 cooling sites (134 indoor, 79 libraries), high schools, 77 community areas; hours and contacts | Data portal; reconciled duplicates across lists |
| **OpenStreetMap** | Buildings, green space, paved land use, roads, walking network | OSM extract |
| **NWS forecast API** | 7-day forecast high, live | Called by the agent at run time |

**Deliberately excluded:** CMAP canopy (no clear reuse license) and CTA transit feeds (licensed for rider
information only). **Race**: never loaded, as the brief recommends; we audit outcomes instead.

**Data quality fixes we can name:** Heat Watch's footprint spilled into suburbs (Berwyn), so cells are
clipped to the 77 community areas. Census drops leading zeros on some tract IDs (we pad them). Every cooling
site is labelled "listed, verify before dispatch" because no source confirms it will be open or air-conditioned
on the day.

## 3. "What were the original model's shortcomings, and why did you switch?"

**One-liner:** "The first model knew what a block looks like, but not where it is, and in Chicago where you are
explains most of the afternoon heat."

- **Original:** BigQuery ML linear regression on physical features: distance to the lake and to the Loop,
  population density, building age, and OpenStreetMap roof, green, paved and road mix. Label = °F hotter or
  cooler than the city average at that spot. 1,207 training cells.
- **Shortcoming 1, accuracy:** on held-out neighborhoods, r² **0.26**, typical error **2.06 °F**. Predicting the
  city average every time gives 2.41 °F, so it barely beat a constant.
- **Shortcoming 2, the diagnostic:** a model with *nothing but a smooth map-position trend* got **0.50**. So the
  biggest signal, a broad south/southwest-hot, lakefront-cool gradient, wasn't in the original features.
- **Why it missed it:** local features are noisy at 330 m (OpenStreetMap tagging is incomplete), and
  "distance to the lake" captures only the shore effect, not the regional gradient.
- **What we changed:** added (1) a coarse location trend (5 terms, a smooth quadratic surface, so it can't
  memorize individual blocks); (2) **satellite surface heat**, which is measured everywhere at the same moment
  and so isn't tied to the volunteers' driving routes; (3) pavement and canopy.
- **How we kept ourselves honest:** a written pass bar set *before* training (beat trend-only by ≥ 0.02 r² on
  the same held-out blocks, or we kill it). Result: **+0.084 → 0.59 r², 1.54 °F**. We backed up the old model
  and kept a one-command rollback before switching the live system.
- **Same model type on purpose:** still linear in BigQuery ML, because `ML.EXPLAIN_PREDICT` gives each site its
  own reasons ("near the lake: −2.1 °F"), which is what the officer sees in the console.

## 3b. "What are the features, and what contributes most?"

Live `heat_offset_lst`, `ML.GLOBAL_EXPLAIN` (average size of each feature's effect, °F) and `ML.WEIGHTS`,
queried 2026-11-19:

| Rank | Feature (plain words) | Avg effect | Direction |
|---|---|---:|---|
| 1 | **Location trend** (5 terms: north-south, east-west, their squares, interaction) | ~1.7 °F (summed) | South hotter than north; the southwest runs hottest |
| 2 | **Distance to Lake Michigan** (7 bands) | 1.45 °F | Within 0.5 km: −3.3 °F; 0.5–1 km: −2.1 °F; 4–8 km inland: +1.6 °F, a ~5 °F swing |
| 3 | **Satellite surface heat** (Landsat, 3×3 cell average) | 0.31 °F | Hotter surfaces → hotter air |
| 4 | Tree canopy within 300 m | 0.03 °F | More trees → slightly cooler |
| 5 | Pavement (impervious %) within 300 m | 0.01 °F | ~0; overlaps with surface heat, which already captures it |

**Say it:** "Where you are and how close you are to the lake explain most of the afternoon air temperature.
Satellite heat adds the block-level detail on top. Trees and pavement barely move *air* temperature on that one
afternoon, which is honest and useful: their payoff shows up in surface heat and the long-term planning question,
not in tomorrow's dispatch."

**The original model, for contrast** (r² 0.26): lake band 1.25, downtown distance 0.67, population density 0.33,
building era 0.30, then road density, paved land use, building share and green share at 0.05–0.07 each. The lake was
already the top feature; what it lacked was the regional gradient.

**Caveats if pushed:** "summed" location is the sum of five average effects, an approximation. Features overlap
(pavement and surface heat), so a small weight means "adds nothing beyond the others," not "doesn't matter."
No demographic column is a feature.

## 4. Likely judge questions

**Rigor / validation**

- *Isn't r² 0.59 low?* "The label is one afternoon of volunteer driving, so there's real noise in it. What
  matters is the ranking and the error: 1.5 °F on neighborhoods the model never saw, against a 20-degree spread
  across the city (−13.9 to +6.7 °F). We report it honestly rather than tuning it on the test set."
- *Why not a random train/test split?* "Neighboring blocks are nearly identical, so a random split leaks:
  the model gets tested on the block next door to one it trained on. We held out whole 2.7 km superblocks, which
  is harder and closer to how it's used."
- *Isn't the location trend just memorizing geography?* "It's five numbers, a smooth tilted surface, so it can't
  memorize blocks. And satellite data, taken across the whole city at the same moment, shows the same
  south/southwest-hot pattern, so it's not just an artifact of the drive."
- *Could the gradient be the order the volunteers drove (afternoon warming)?* "Possibly in part, and we say so.
  Heat Watch has no timestamps to correct it. Landsat is our independent check, and it agrees."
- *Why linear and not XGBoost or a neural net?* "1,207 training cells, and the duty officer needs a reason for
  each site. Linear gives exact per-site attributions in BigQuery. A tree model is the obvious next experiment,
  judged on the same held-out blocks and the same pass bar." (Don't claim we tested one; we didn't.)
- *How do you predict where there were no readings?* "Every input is available citywide, so the model scores
  all 5,053 cells. We checked that no unmeasured cell predicts outside the range we actually observed."
- *How would you know it's working in the real world?* "Next validation: compare against 311 heat calls and
  heat-related EMS runs by area. We don't have outcome data today, so we audit coverage instead."

**The prediction itself**

- *Why an offset, not a temperature?* "Heat Watch is one day. What carries over to another day is the
  difference between blocks. Tomorrow's NWS forecast high plus the block's offset gives an expected afternoon air
  temperature for that block."
- *Isn't satellite surface temperature different from air temperature?* "Yes. Sunlit pavement at 10:30 a.m. runs far
  hotter than the air. That's why it's only an input, never shown as a temperature. The label is always
  measured air."
- *What does the model actually say drives heat?* "Biggest: location (about 1.7 °F of average influence), then
  the lake (about 1.5 °F), then surface heat. Pavement and canopy add little to *air* temperature on that
  afternoon; they matter more for the long-term tree-planting question."

**Equity**

- *How is vulnerability defined?* "Three Census measures the Today track cares about: people 65+, households
  without a car, and poverty. Each becomes a percentile across the city's tracts and they're averaged with equal
  weights, not tuned on outcomes."
- *If race isn't used, how do you know it's fair?* "Removing a column doesn't remove bias, because proxies remain. So
  we check the output: the top sites land in the most vulnerable quarter of tracts at 4.3× the city rate, and
  nearby residents are 23% 65+ versus 14% citywide. If the plan systematically missed vulnerable areas, the audit
  would show it."
- *Heat vs. vulnerability, how are they weighted?* "Site ranking blends predicted heat with vulnerability
  (default 50/50), and the officer can adjust it in the console."

**Data and operations**

- *How current is the data?* "ACS 2024 5-year (surveys 2020–24), NLCD 2021, Landsat summers 2019–25, Heat
  Watch's Chicago campaign, and the forecast is live."
- *Does it work in another city?* "The pipeline is SQL templates with a city config file. Heat Watch has run in
  dozens of US cities, and Landsat and NLCD cover the whole country. Chicago-specific pieces are the city facility
  lists."
- *Reproducible?* "Every number on the slide comes from SQL in the repo; the data is backed up with
  row-count checks and a restore script."
- *Cost?* "BigQuery ML trains in seconds inside BigQuery, with no separate ML infrastructure."
- *What's next?* "Validate against 311/EMS outcomes; then the two longer-horizon questions from the brief:
  where to fund permanent cooling centers, and where trees or cool pavement buy the most relief per dollar."

## 5. Ties to the demo

In yesterday's recording the operator asks why one neighborhood is hotter than the Loop and the agent answers
"lake ... surface trends". That answer **is** `ML.EXPLAIN_PREDICT` from the new model (location trend, lake band,
surface heat). Point at it if a judge asks "where is the ML in the product?"
