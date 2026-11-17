# Physical enrichment model comparison

This implements preparation for priority 5 in `docs/DATA_WORK_PLAN.md`. **No candidate has been trained by this preparation step.** The default command writes a local SQL/report plan. Executing it requires the working dataset's priorities 3 and 4 to be recorded as validated by `WorkRunner`.

## Inputs and fixed evaluation cohort

- Candidate context: `qwiklabs-gcp-04-3e58e01275b8.a4i_climate_work_v2.site_context_enriched`.
- Frozen labels, grid and historical models: `qwiklabs-gcp-04-3e58e01275b8.a4i_climate_backup_20261117_214035`.
- All **5,053** existing cell IDs remain represented.
- Training: `n_obs >= 8`, `is_holdout = FALSE`, finite/non-null `offset_f`: **1,207 cells**.
- Evaluation: `n_obs >= 8`, `is_holdout = TRUE`, finite/non-null `offset_f`: **442 cells**.
- Candidate label: **air-temperature offset `offset_f`, in degrees Fahrenheit**. Neither a satellite surface temperature nor a future forecast is used as the target.

Preflight rejects changed IDs, labels, observation counts or spatial split; incorrect cohort counts; duplicate or missing cells; missing canopy/impervious values; and invalid percentage units. Prediction checks reject missing model outputs, inconsistent evaluation IDs/labels, or differing all-cell ranking populations. Missing predictions cannot silently reduce an evaluation cohort.

All holdout results are **exploratory** because multiple previous experiments used the same spatial holdout. This comparison is not a fresh independent test, and no candidate is automatically promoted.

## Candidate: physical land cover

`heat_offset_candidate` is a BigQuery ML `LINEAR_REG` with L2 regularization of 1, global explanations, and `NO_SPLIT`. Its training query explicitly excludes held-out cells; preprocessing is stored in the model's `TRANSFORM` clause.

Raw inputs:

- Distance to lake and downtown, using the original bucket boundaries.
- Housing construction year, bucketized to the original building eras.
- Existing OSM building, green-space and paved-use shares; log road density.
- **2021** NLCD/USFS canopy and impervious surface percentages at the cell and 300 m buffer scales, converted to fractions inside `TRANSFORM`.

The candidate excludes population density, age demographics, poverty, income, race, disability and vulnerability rankings. Feature selection is an explicit allowlist; its training query names every selected field rather than using `SELECT *`. Physical proxies can still reflect unequal development patterns; excluding a demographic column does not establish unbiased outcomes.

The regularization, bucket boundaries and feature set are fixed before examining this run's holdout results. There is no holdout-driven parameter search in the script. Similar center/buffer land-cover variables can be correlated; individual linear attributions should be interpreted with that limitation.

## Optional candidate: satellite predictor

`--include-lst` additionally prepares `heat_offset_candidate_lst`, using the same physical inputs and:

- A Landsat surface-offset predictor, usable only with at least **five scenes**, a finite offset, and a finite nonnegative IQR.
- The usable surface-offset IQR as a measurement-variability predictor.
- An indicator for unavailable or unreliable surface data.

Unusable surface values are masked to null inside `TRANSFORM`. BigQuery ML's numeric missing-value handling uses a training-derived mean, accompanied by the explicit unavailable indicator. No training or evaluation cells are dropped because their satellite value is missing or has too few scenes. The five-scene threshold is a coverage screen, not a validated uncertainty bound.

These surface inputs summarize **2019–2025 morning Landsat scenes**. They are predictors for the air-offset target. A surface offset is never added directly to a weather forecast. Temporal alignment to the street campaign remains unresolved, and this candidate should be interpreted as a separate exploratory comparison. Observational coefficients do not identify the causal temperature effect of planting trees or replacing pavement.

## Historical comparisons

The script scores these four copied historical models, without retraining them:

| Model | Historical training cohort | Schema notes |
|---|---:|---|
| `heat_offset_model` | 1,207 cells | Original physical features plus the population-density proxy |
| `exp_bt_phys` | 1,207 cells | Boosted-tree regressor with lake/downtown distance, population density and housing age |
| `heat_offset_v2` | 1,283 cells | Latitude/longitude and log-distance features; original population-density proxy |
| `heat_offset_v3` | 1,283 cells | Additional spatial interactions/squares, distance transforms and built-environment combinations |

Original training SQL was recovered through **read-only** BigQuery job metadata and saved in `data/enrichment/model_evaluation/baseline_training_queries.json`.

The v2/v3 queries used all non-null air labels with a `CUSTOM is_holdout` split, including lower-observation cells. Their original internal holdout had 457 cells. This run scores the agreed 442-cell subset, while their training cohort includes 76 additional low-observation training cells compared with the new candidate. Therefore this comparison combines differences in features, algorithms and historical training cohorts; it is not a controlled attribution of improvement to canopy alone.

Recovered expressions are reproduced exactly: v2 uses `LN(distance + 0.5)`; v3 uses `LN(distance + 0.2)`, lake-distance square root, latitude/longitude interaction, squared coordinates centered at the Loop, and the original impervious combinations. Both use the original 1950 fallback for invalid housing construction years.

Historical predictions use the **backup context's original feature values**. This prevents revised ACS population estimates or corrected tract assignments from silently changing historical model inputs. Metadata checks enforce the expected historical feature/label schema before scoring.

## Metrics and ranking stability

For each historical/candidate model and the constant-zero offset baseline, the script records:

- MAE, RMSE, signed mean error, and R² on identical held-out air labels.
- R² computed as `1 - SSE / SST`, where SST is centered on the **held-out mean**. The zero-offset baseline's R² is calculated and may be negative; it is not hardcoded to zero.
- Spearman correlation of predicted heat ranks against `heat_offset_model` over all 5,053 cells, using average ranks for ties.
- Overlap of the hottest 20 predicted cells with the original model's hottest 20. Ties are resolved by cell ID. Constant predictions have null ranking-stability metrics because their apparent top 20 would be arbitrary.

Ranking stability here measures **heat-only rankings**, not final dispatch priorities, vulnerability weighting or spaced truck-stop selection. An operational ranking comparison remains a separate consumer check.

## Campaign date investigation

Verified evidence is saved locally under `data/enrichment/model_evaluation/`:

- The challenge README and notebook identify Chicago's **afternoon NOAA/CAPA traverse layer 75** and explain that timestamps are unavailable for many campaigns.
- [NOAA layer metadata](https://gis.nnvl.noaa.gov/arcgis/rest/services/HINDZ/Afternoon_Traverses_in_Cities/MapServer/75?f=json) lists only `FID`, geometry, temperature, relative humidity and heat index; there is no observation-date field.
- The layer's [XML metadata](https://gis.nnvl.noaa.gov/arcgis/rest/services/HINDZ/Afternoon_Traverses_in_Cities/MapServer/75/metadata?f=xml) contains an Esri metadata creation date of **2026-10-26**, with no campaign date.
- Authenticated, read-only metadata for `gs://class-demo/a4i-2026/challenge-1-walkability/chicago/heat_observations/000000000000.parquet` records export/object creation on **2026-08-08**, without custom campaign-date metadata. The unauthenticated object listing required authorization; no object data were changed.
- The recovered BigQuery table description says single-day afternoon campaign; all `observed_at` values remain null.

**The campaign date is unresolved.** Metadata creation, export, model training and file-copy dates are not observation dates. The script records `campaign_date = null` and the unresolved status in every model-validation row. Station-history example dates in the tutorial are not accepted as evidence of the campaign date.

## CLI and output schema

```bash
# Preparation only: local plan and SQL files; no BigQuery writes.
.venv/bin/python scripts/evaluate_enriched_model.py --include-lst

# After priorities3/4 validate, explicitly execute training and comparison.
.venv/bin/python scripts/evaluate_enriched_model.py --include-lst --execute

# Re-score existing candidates after feature-schema checks, without training.
.venv/bin/python scripts/evaluate_enriched_model.py --include-lst --execute --score-only

.venv/bin/python -m pytest tests/test_enriched_model.py -q
.venv/bin/python -m mypy --follow-imports=silent --ignore-missing-imports scripts/evaluate_enriched_model.py
```

Every BigQuery write goes through `WorkRunner`, which rejects destructive SQL and write destinations outside the working dataset, records job IDs/results locally, and uses a **5 GB query budget**. Candidate creation uses `CREATE MODEL` without replacement; existing candidates require the explicit scoring-only path. Original model names and consumer configuration remain untouched.

Working output tables:

- `model_predictions`: one row per model/cell, with run/model IDs, predicted and observed **air** offsets, observation count, original holdout flag, evaluation-cohort flag and model role.
- `model_validation`: one row per comparison model, with run/model reference, role, feature policy, training/evaluation counts, metrics, ranking stability, exploratory status, campaign-date status and limitations.

The script exports `PREDICTION_SCHEMA`, `MODEL_SCHEMA` and `SCHEMAS`. `data/enrichment/model_evaluation/plan.json` includes output field schemas and SQL hashes; execution saves `model_validation.json` and records priority5 evidence in the shared working progress report.
