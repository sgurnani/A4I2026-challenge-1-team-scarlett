## Speaker notes to read: the ML model in plain words

Presenter's version, with the two phrases that must change marked:

The machine learning model predicts how much hotter or cooler each block runs than the city average. To do that it looks at where the block sits, how close it is to the lake, how hot its rooftops and pavement get in satellite images, and how much tree canopy is around it.

On neighbourhoods it never saw during training, it's typically within about one and a half degrees. ~~That's more than twice as accurate as the original model.~~

Better data, a tested model, and an audited outcome. That's what makes the answer ~~trustworthy~~ for a dispatcher.

**Use instead (same length):**

The machine learning model predicts how much hotter or cooler each block runs than the city average. To do that it looks at where the block sits, how close it is to the lake, how hot its rooftops and pavement get in satellite images, and how much tree canopy is around it.

On neighbourhoods it never saw during training, it's typically within about one and a half degrees, and it captures the big pattern our first model missed: cool by the lake, warmer inland and to the southwest. What one afternoon of data can't do is tell one block from the next, so heat is only half the score, and we audit who the plan reaches.

Better data, a tested model, and an audited outcome, with the limits stated. That's what a dispatcher can act on.

Why: "twice as accurate" is the r² ratio (2.3×); the error only fell ~25% (2.06 → 1.54 °F), and the ranking skill is weak (see the next section). "Trustworthy" invites the question you can't win.

**What the speaker notes mean (if asked):**

- **"One afternoon of data":** every street-level temperature comes from NOAA Heat Watch, volunteers driving sensors on one hot afternoon (48,855 readings). That is what the model learned from.
- **"Can't tell one block from the next":** the model learned the city-wide pattern well (cool by the lake, warmer inland and to the southwest; several °F, seen in thousands of readings). Neighbouring blocks differ by about a degree or less, which is the size of the noise in one afternoon (clouds, time of the drive, sensor quirks). Held-out rank agreement within neighbourhoods is 0.08, close to none. Good neighbourhood-scale map, not a block-by-block ranking.
- **"Heat is only half the score":** site score = (½ × heat percentile + ½ × vulnerability index) × access factor. The other half and the access boost come from more reliable data (Census, distance to cooling), so the model's block-level weakness can't decide a site on its own.
- **"We audit who the plan reaches":** after sites are picked, the equity audit compares who lives in those tracts (income, poverty, 65+, no car, share in the most vulnerable tracts) with the city. Ranked by heat alone, no sites land in the most vulnerable tracts; with the blended score, half do, 4.3× the city rate.
- **Short spoken version:** "One afternoon of data shows the big pattern, not block-by-block detail, so heat is only half the score, and we check who the plan actually reaches."

**Who devised the site score, and how it is built:**

- **We formulated it; it was not given.** The challenge README supplies the data and asks us to audit the outcome (rank sites, look up who lives there, compare to the city) but specifies no scoring formula. Ours was introduced in the original dispatch agent (commit `f896fb2`, Nov 17) and recorded as a design decision in `docs/ARCHITECTURE.md`.
- **Formula:** priority = (heat_weight × heat percentile + (1 − heat_weight) × vulnerability index) × (0.6 + 0.4 × access gap).
- **Heat term:** the model's predicted offset (°F hotter than the city average), turned into a citywide percentile.
- **Vulnerability index:** equal-weight average of the percentile ranks of share 65+, households without a car, and poverty (`sql/02`); missing values set to the city median. Race is never loaded, as the README recommends. "Most vulnerable" = index ≥ 0.75, about 1 in 9 tracts (11.7%).
- **heat_weight:** default 0.5 (balanced); the officer can change it in the console (vulnerable-first preset = 0.2) and the equity audit shows the effect immediately.
- **Access factor:** 0.6 next to an indoor cooling site, rising linearly to 1.0 at 3 km or more, so a site far from cooling can score up to two thirds higher.
- **Not fitted to data:** no outcome tuning, because there is no outcome data (heat deaths, EMS calls) to fit against. The weights are transparent policy choices.
- **If asked "why those weights?":** "They're policy choices, not learned parameters. We have no outcome data to fit against, so we picked simple, explainable defaults: heat and vulnerability count equally, and distance to cooling can lift a site's score by up to two thirds. The officer can move the heat weight, and the equity audit shows the effect right away. That audit is how we learned that ranking by heat alone misses the most vulnerable tracts."

## The one thing to internalise first

The table on the slide is computed correctly (r² 0.26 → 0.50 → 0.59 on 442 held-out cells). But it means less than it looks:

- Most of the gain over "trend only" comes from **one cold lakefront area** (Rogers Park, 19 cells, −6 to −14 °F), which the new model gets right because it knows distance to the lake.
- **Within** neighborhoods the model can't reliably order blocks. On held-out cells its rank agreement with measured heat is 0.08 (original 0.21), and outside Rogers Park guessing the city average (1.38 °F) is as good as any model (new 1.42 °F).
- So the honest story, and a stronger one for "rigor", is: **the model gets the big pattern right (cool by the lake, warmer inland and to the southwest); one afternoon of volunteer driving can't resolve block-to-block detail; that's why heat is only half of the site score, and why we audit the result.**

Never say: "what matters is the ranking", "more than twice as accurate", "trustworthy", "not tuned on the test set", "most vulnerable quarter of tracts".

## 0. Fixes on the Sources slide before recording

| Slide text | Problem | Replace with |
|---|---|---|
| "Tested on unseen areas:" (empty) | Bullet has no text | "Tested on unseen areas: 24 areas ~2.7 km across held out of training (442 cells)" |
| "Pass bar set in advance" | Bar was written 37 min before training the shipped model, but earlier checks used the same held-out areas | "Pass bar written down before training the shipped model" |
| "Data corrected: 26% of grid cells re-assigned" | The fix was matching each cell to the tract it sits in (not the nearest tract centre) | "Data corrected: 26% of grid cells matched to the tract they actually sit in" |
| "Equity audit: ... 4.3× the city rate" | Half the site score *is* vulnerability, so this is partly by construction | "Equity audit: heat alone would miss the most vulnerable tracts; our blend reaches them at 4.3× the city rate" |
| "23% of nearby residents are 65+" | It's residents of the tracts containing the sites | "residents of those tracts" |
| "City of Chicago: 995 ... records" next to "287 cooling sites" | Two counts side by side | "287 listed cooling sites (134 indoor), plus every school: 995 places" |
| "Excluded on purpose: CMAP canopy, CTA transit" | Both are loaded in BigQuery (as comparison/reference), just not used | "Kept out of the model and product: CMAP canopy (license unclear), CTA transit (rider-info only)" |
| Footer "New model = location trend + satellite surface heat + pavement and canopy" | Leaves out the lake, its biggest single input | "... = location trend + distance to the lake + satellite surface heat + land cover" |
| "2.06 F" etc. | Missing degree sign | "2.06 °F" |
| No limitations line anywhere in the deck | Judges are told to reward stated limits | Add: "Limit: one afternoon of readings gets the big pattern right, not block-by-block detail, so heat is half the score and every stop is confirmed by an officer." |

Also: the right-column dashes are in a code font; the footer is unreadable on video; slide 7 has "STORESES".

## 0b. "Why Chicago?"

Checked 2026-11-19 against the challenge README and the original sources. Corrections from the first draft are marked ⚠.

**Data reasons (from the challenge guide)**

- **Abundant street-level data:** Chicago's Heat Watch campaign has ~49,000 readings (48,855 in our table).
- **Second-largest of the 8 recommended cities** (68 are available in total; the guide recommends 8 as tested end to end). Houston, 147,000, Chicago 49,000, Seattle 35,000, Dallas (the notebook default) 19,000.
- **Volume without the penalty:** the guide calls Houston "the largest campaign by far, and the slowest to load." Chicago gives a third of Houston's volume, enough to hold out 442 cells for testing and still train on 1,207.

**Why the problem matters there**

- **Warming:** NOAA records for the Chicago Midway Airport 3 SW station show the annual mean temperature up **2.12 °C (about 3.8 °F)** from 1942–1951 to 2016–2025. Source: ClimateObservatory.org's analysis of NOAA station data (climateobservatory.org/chicago-us). Say "about 4 degrees Fahrenheit" to a US audience.
- **Intense urban heat island** ⚠ use the 2024 report consistently: in Climate Central's *Urban Heat Hot Spots in 65 Cities* (2024), Chicago's per-capita average UHI index is **8.7 °F, 4th highest of 65 cities** (behind New York, San Francisco and Newark). (The 8.3 °F figure is from the 2023 report of 44 cities, where Chicago tied Miami for 3rd; don't mix the two.)
- **High exposure:** same 2024 report: **1.7 million residents (62%)** live where the UHI index is 8 °F or more.
- **Regional vulnerability:** CMAP (Oct 2026): **44% of northeastern Illinois** lives in census tracts with a Heat Vulnerability Index score of 5.7/10 or above.
- **Where vulnerability comes from** ⚠ CMAP splits this by county: in **Cook County**, extensive impervious surfaces intensify the heat island; **DuPage, Kane, Kendall and McHenry** have more residents 65+ and higher rates of diabetes, asthma and coronary disease. Don't attribute the health and age points to Cook.
- **Future warming** (verified): "Chicago is projected to warm by 9.1 °F, with summers more like Montgomery, Ala." by 2100. Source: Climate Central, *Shifting U.S. Cities* (July 13, 2022), high-emissions scenario SSP3-7.0 (updated in *Shifting Cities: Mapping Future Climates*, July 2025). For comparison in the same analysis: Houston +6.4 °F (like Lahore, Pakistan), New York +7.6 °F (like Columbia, S.C.), so Chicago's projected warming is the largest of the three. Always say "if emissions stay high"; a judge may ask.
- **And the history:** the 1995 heat wave (the deck's 6.7× stat), which is why Chicago is the textbook case for "help has to come to the people who can't leave home."

**Say it in 20 seconds**

We chose Chicago for the data and for the stakes. It has the second-largest street-level heat campaign in the challenge, about 49,000 readings, without Houston's load time. And it needs this: it's warmed about 4 °F since the 1940s, it has the 4th most intense urban heat island of 65 US cities, and 1.7 million people, 62% of the city, live in its hottest zones. If emissions stay high, Climate Central projects Chicago summers will feel like Montgomery, Alabama's by 2100.

**If a judge pushes:** "You excluded CMAP data but cite CMAP?" We kept CMAP's *canopy dataset* out of the model and the product because its redistribution license is unspecified (it's loaded only as a comparison column); quoting a published finding with attribution is different.

## 1. Data slide script (~75 seconds, ~200 words)

The challenge gave us three tables: **NOAA Heat Watch**, 48,855 street-level air readings from one hot afternoon; seven years of **weather-station** highs; and **Census** demographics.

We brought in everything marked with a star: **satellite surface heat** from Landsat, across the whole city at once; **pavement and tree canopy**; the **2024 Census**, with every grid cell matched to the tract it actually sits in, which corrected a quarter of them; and the **City's 287 listed cooling sites**. Licenses checked, and **race is never loaded**.

On the right is the model. Our first version explained **26%** of the variation on areas it never saw. A simple map-position trend got **50%**, so it was missing the city-wide pattern. We wrote down a pass bar before training the new one, and it reached **0.59**, typical error **one and a half degrees**.

We're honest about the limit: it gets the **big pattern** right, cool by the lake and warmer to the southwest, but one afternoon of data can't rank one block against the next. So heat is only half of each site's score. And our audit showed why that matters: **ranked by heat alone, the trucks would have gone to better-off neighborhoods**, none in the most vulnerable tracts. Blending in who lives there puts half of them in the most vulnerable tracts, **4.3 times** the city rate.

Shorter fallback (45 s): paragraphs 1 and 3, then the last paragraph from "ranked by heat alone".

## 2. "What data did you collect outside the provided notebook?"

The notebook loads three tables: `heat_observations` (Heat Watch), `station_daily_temps` (GHCN-Daily), `tract_demographics` (ACS). Everything else we acquired:

| Source | What it gave us | How |
|---|---|---|
| **Landsat 8/9** (USGS), summers 2019–2025, 14 clear scenes | Surface temperature for every cell, citywide, at one moment | Earth Engine inside BigQuery (`ST_REGIONSTATS`), cloud pixels masked |
| **NLCD 2021** (USGS/USFS) | Impervious surface % and tree canopy % within 300 m | Same, Earth Engine in BigQuery |
| **ACS 2024 5-year + 2024 tract boundaries** | Current 65+, no-car, poverty; each cell matched to the tract polygon it sits in (1,305 of 5,053 cells changed tract vs. the old nearest-centroid match) | BigQuery public data + Census TIGER |
| **City of Chicago open data** | 287 listed cooling sites (134 indoor, 79 libraries, 110 outdoor spray features); 995 places in total incl. schools; 77 community areas | Data portal; duplicates reconciled across lists |
| **OpenStreetMap** | Buildings, green space, roads, walking network (used by the original model and walk-access, not the live model) | OSM extract |
| **NWS forecast API** | 7-day forecast high, live | Called by the agent at run time |

**Kept out of the model and the product:** CMAP tract canopy (redistribution license unspecified; loaded only as a comparison column) and CTA transit feeds (licensed for rider information). **Race:** never loaded; we audit outcomes instead.

**Data quality fixes we can name:** Heat Watch's footprint spilled into suburbs (Berwyn), so cells are clipped to the 77 community areas. Cells were assigned to the nearest tract *centre*; we switched to the tract polygon they sit in (26% changed). Census drops leading zeros on some tract IDs (padded). Every cooling site is labelled "listed, verify before dispatch" because no source confirms it will be open or air-conditioned on the day.

## 3. "What were the original model's shortcomings, and why did you switch?"

**One-liner:** "The first model knew what a block looks like, but not where it is. In Chicago, where you are, and how close to the lake, explains most of the afternoon heat."

- **Original:** BigQuery ML linear regression on physical features: distance to the lake and to the Loop, population density, building age, and OpenStreetMap roof, green, paved and road mix. Label = °F hotter or cooler than the city average at that spot. 1,207 training cells.
- **Shortcoming 1, accuracy:** on held-out areas, r² **0.26**, RMSE **2.06 °F** vs 2.41 °F for always guessing the city average. Its *average* error (1.44 °F) was no better than guessing the average.
- **Shortcoming 2, the diagnostic:** a smooth map-position trend alone got **0.50**: the biggest signal, a regional gradient (warmer inland and to the southwest, cool by the lake), wasn't in its features.
- **Why it missed it:** local features are noisy at 330 m (OpenStreetMap tagging is incomplete).
- **What we changed:** (1) a coarse location trend (5 terms, a smooth surface, can't memorize blocks); (2) distance-to-lake bands; (3) **satellite surface heat**, measured everywhere at the same moment; (4) pavement and canopy.
- **How we kept ourselves honest:** we wrote the pass bar (beat trend-only by ≥ 0.02 r² on the same held-out areas, or kill it) into the design doc and git **before training the shipped model**. Result **+0.084 → 0.59, 1.54 °F**. Caveat to volunteer if asked: earlier exploratory checks used the same held-out areas (trend + satellite already showed 0.56 vs 0.51), so treat 0.59 as slightly optimistic. Old model backed up; one-command rollback.
- **Same model type on purpose:** linear in BigQuery ML, because `ML.EXPLAIN_PREDICT` gives each site its own reasons ("near the lake: −2.1 °F"), which the officer sees in the console.

## 3b. "What are the features, and what contributes most?"

Live `heat_offset_lst`, `ML.GLOBAL_EXPLAIN` (average size of each feature's effect, °F) and `ML.WEIGHTS`:

| Feature (plain words) | Avg effect | Direction |
|---|---:|---|
| **Distance to Lake Michigan** (7 bands): biggest single feature | 1.45 °F | Within 0.5 km: −3.3 °F; 0.5–1 km: −2.1 °F; 4–8 km inland: +1.6 °F, a ~5 °F swing |
| **Location trend** (5 terms; largest single term 0.66) | ~1.7 °F if summed | Cooler to the north; warmer to the southwest |
| **Satellite surface heat** (Landsat, 3×3 cell average) | 0.31 °F | Hotter surfaces → hotter air |
| Tree canopy within 300 m | 0.03 °F | More trees → slightly cooler |
| Pavement (impervious %) within 300 m | 0.01 °F | ~0; overlaps with surface heat |

**Say it:** "The lake and where you are in the city together explain most of the afternoon air temperature; satellite heat adds a little on top. Trees and pavement barely move *air* temperature in this data; their payoff shows up in surface heat and the long-term planning question, not tomorrow's dispatch."

**Original model, for contrast:** lake band 1.25, downtown distance 0.67, density 0.33, building era 0.30, the OSM features 0.05–0.07 each. It had the lake; it lacked the regional gradient.

**Caveats if pushed:** summing five average effects is approximate; overlapping features (pavement, surface heat) mean a small weight is "adds nothing beyond the others". No demographic column is a feature.

## 4. Likely judge questions

**Rigor / validation**

- *Isn't r² 0.59 low?* "Yes, and we don't oversell it. One afternoon of volunteer driving is a noisy label. The model gets the big pattern right, the lake and the regional gradient, which is where most of the r² comes from. It can't reliably rank one block against the next inside a neighborhood. That's why heat is half the score, not all of it."
- *Does it rank blocks correctly?* "Not reliably within a neighborhood: on held-out areas rank agreement is weak (0.08; the original's 0.21 isn't good either). It's reliable at the city scale: lakefront vs inland, north vs southwest. We use it for that and let vulnerability and access drive the rest."
- *Is +0.084 significant? How many held-out areas?* "24 areas, 442 cells. A resampling check over areas gives a wide interval, and most of the gain is one lakefront area. We treat the pass bar as a sanity gate, not a significance test."
- *Was the bar really set in advance?* "Written into the design doc and committed before we trained the shipped model; it's in git. Our earlier exploratory checks used the same held-out areas, so 0.59 is slightly optimistic."
- *Why not a random split?* "Neighboring cells are nearly identical, so a random split leaks. We held out 24 whole areas about 2.7 km across. They sit among training areas, so it's filling gaps inside the driven area, not predicting a new city."
- *Isn't the location trend just memorizing geography? Could it be drive order?* "It's five numbers, a smooth surface, so it can't memorize blocks. Part of the north-south gradient could be the order volunteers drove; Heat Watch has no timestamps to correct it. The satellite, measured everywhere at once, independently shows the **southwest** running hottest, but not the whole south, so we state that limit."
- *How much does the satellite add by itself?* "Early check: trend + surface heat 0.56 vs 0.51 for trend alone, about +0.05. Most of the rest comes from the lake bands. A clean trend + lake comparison is next."
- *Why linear, not XGBoost?* "1,207 training cells and the officer needs a reason per site; linear gives exact attributions in BigQuery. A tree model is the next experiment, same held-out areas, same bar." (We haven't tested one.)
- *Unmeasured cells?* "Every input exists citywide, so all 5,053 cells are scored; none predicts outside the measured range."
- *Real-world validation?* "Next: compare against 311 heat calls and heat-related EMS runs. No outcome data today, so we audit coverage instead."

**The prediction itself**

- *Why an offset, not a temperature?* "Heat Watch is one day. What carries over is the difference between blocks. Tomorrow's NWS forecast high plus the block's offset gives an expected afternoon air temperature."
- *Satellite surface vs air temperature?* "Sunlit pavement at 10:30 a.m. runs far hotter than the air. So it's only an input, never shown as a temperature; the label is always measured air."
- *Typical error vs the spread?* "Typical error (RMSE) 1.5 °F against a typical spread of 2.4 °F across held-out cells." (Don't compare with the −13.9 to +6.7 range; that's a few extreme lakefront cells.)

**Equity**

- *How is vulnerability defined?* "Three Census measures the Today track acts on: 65+, no car, poverty. Each becomes a percentile across tracts, averaged with equal weights, not tuned on outcomes."
- *Your top sites are vulnerable because you ranked by vulnerability. What does heat alone do?* "That's exactly what our audit is for. Ranked by heat alone, the top 20 sites land in **none** of the most vulnerable tracts, with poverty 11.6% vs 16.8% citywide and car-less households 13.6% vs 26.1%. The default 50/50 blend puts 8 of the 16 tracts they fall in among the most vulnerable, 4.3× the city rate. The officer's slider shows the trade-off."
- *What's "most vulnerable"?* "Vulnerability index ≥ 0.75, about 1 in 9 tracts citywide (11.7%). Not a quartile."
- *Why 16 tracts for 20 sites?* "Some tracts hold two sites; the audit counts people once per tract. It's a small sample, and we say so."
- *Why show 65+ and poverty but not no-car?* "No-car is 28.0% vs 26.1%, close to citywide. Income is the starker one: $48k vs $82k." (Have all four ready.)
- *How are heat and vulnerability combined?* "Score = (½ heat percentile + ½ vulnerability) × an access factor (0.6–1.0, higher the farther from indoor cooling). The weights are a neutral default, adjustable in the console."
- *Did you check race?* "We chose not to load it at all; the brief allows it as an after-the-fact audit, and it's an easy addition. We audit the three vulnerabilities the Today track acts on."
- *ACS margins of error?* "Kept in the table, not yet used in the index. Small tracts can swing; the officer sees tract population next to each site."

**Data and operations**

- *How current?* "ACS 2024 5-year (surveys 2020–24), NLCD 2021, Landsat summers 2019–25, Heat Watch's Chicago campaign, live NWS forecast. (The demo uses a historic hot-day toggle because it's November.)"
- *Another city?* "SQL templates plus a city config file. Heat Watch has run in dozens of US cities; Landsat and NLCD cover the country. The facility lists are Chicago-specific."
- *Reproducible?* "Every number comes from SQL in the repo; the data is backed up with row-count checks and a restore script."
- *Cost?* "BigQuery ML trains in seconds inside BigQuery, with no separate ML infrastructure."
- *What's next?* "A tree model and a clean ablation on the same held-out areas; validate against 311/EMS outcomes; then the brief's longer questions: permanent cooling centers, and where trees or cool pavement buy the most relief."

## 5. Ties to the demo

In yesterday's recording the operator asks why one neighborhood is hotter than the Loop and the agent answers "lake ... surface trends". That answer **is** `ML.EXPLAIN_PREDICT` from the new model (lake band, location trend, surface heat): the city-scale pattern the model is good at. Point at it if a judge asks "where is the ML?"

## 6. "Were the test areas contiguous?"

- **No, not by design.** Each ~2.7 km square was held out or kept independently, by a pseudo-random hash of its grid position (`sql/01_grid_cells.sql`). Nothing forced the test squares together or apart.
- **By chance they form patches.** 24 held-out areas (442 cells, 25% of the 97 measured areas). 19 of the 24 share an edge with another held-out area, giving 12 separate patches: one of 5 areas (99 cells, about 8 km across, South Side), two of 3, four of 2, and five single areas. That much clustering is normal at a 25% rate.
- **Patches make the test slightly harder, not easier.** Cells in the middle of a patch are farther from any training data than cells in a lone square.
- **Still gap-filling.** Every held-out area borders training areas on at least two sides (4.9 on average), so the model fills holes inside the driven area; it does not predict a new region.
- **Evidence is thinner than "24" suggests.** Treat it as 24 areas in 12 independent patches, which supports "wide interval, sanity gate, not a significance test".
- **Rogers Park,** the area behind most of the r² gain, is one of the isolated single squares at the far north.
- **Spoken answer:** "No. Each 2.7 km square was held out at random, so they ended up as 24 areas in 12 separate patches, all surrounded by training areas. It's a fair test of filling gaps inside the city, not of predicting a new one, and with 12 patches we treat the result as a sanity check, not proof."

## 7. "What time periods does the data cover?"

Checked against BigQuery on 2026-11-19.

**Data the model learns from:**

- **Street-level heat (NOAA Heat Watch, provided):** one summer afternoon, 48,855 readings in three one-hour windows. The campaign recorded no clock times (`observed_at` is empty in every row, which the brief says is expected), and its date is not in the data. This is the label the model predicts.
- **Satellite surface heat (Landsat, ours):** 14 clear summer scenes, June 2019 to June 2025, all taken in June, July or August; each cell averages about 13 scenes. A multi-year summer average, not the campaign day.
- **Pavement and tree canopy (USGS NLCD, ours):** 2021 release (imperviousness and tree canopy cover).
- **Distance to the lake and map position:** fixed geography, no date.

**Data the score and audit use:**

- **Demographics (Census ACS, ours):** 2024 release, the 5-year estimate covering 2020-2024, on 2024 tract boundaries (799 Chicago tracts). The challenge-provided table was the older ACS 2022 vintage.
- **Weather-station history (NOAA GHCN-Daily, provided):** daily highs and lows from Jan 1 2019 to Dec 31 2025 (seven years), 13 stations around Chicago including O'Hare and Midway. Answers "how often does it get this hot?", not "where is it hotter?".
- **Cooling sites, libraries, schools (City of Chicago portal, ours):** snapshots downloaded on Nov 17 2026.
- **Forecast (National Weather Service, ours):** the 7-day forecast pulled on Nov 17 2026 (Nov 17-24). In the product, tomorrow's expected temperature = forecast high + the model's offset.

**Kept out of the model (comparison only):** CMAP tract canopy (2023 imagery), CTA transit schedule (Oct 29 to Dec 31 2026).

**If a judge asks "the years don't line up, is that a problem?":** "The label is one afternoon, but the features are slow-changing physical things: pavement, tree cover, distance to the lake, and a multi-year summer average of surface heat. Those barely move over a few years, so they describe the same city the volunteers drove. The demographics are the newest Census release, because the plan is for today's residents."

**Spoken version:** "The heat label is one summer afternoon of street readings. The model's inputs are a 2019 to 2025 summer satellite average and 2021 land cover. Demographics are the 2020 to 2024 Census, and the station history covers 2019 to 2025 to answer how often it gets this hot."
