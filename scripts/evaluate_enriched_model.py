#!/usr/bin/env python3
"""Prepare or run an exploratory physical-feature air-offset model comparison.

Default is a LOCAL SQL/report plan. --execute requires validated priorities3/4
and uses WorkRunner for every BigQuery write, bounded to5GB per query job.
Original models are only scored from the recovery dataset; none is promoted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MIN_OBS = 8
EXPECTED_CELLS = 5053
EXPECTED_TRAIN = 1207
EXPECTED_HOLDOUT = 442
HISTORICAL_CUSTOM_TRAIN = 1283
BASELINE_MODELS = ("heat_offset_model", "heat_offset_v2", "heat_offset_v3", "exp_bt_phys")
PHYSICAL_FEATURES = (
    "dist_to_lake_km", "dist_to_loop_km", "median_year_structure_built",
    "building_share", "green_share", "paved_use_share", "road_km_per_km2",
    "tree_canopy_pct", "impervious_pct", "tree_canopy_pct_300m", "impervious_pct_300m",
)
LST_FEATURES = ("lst_offset_f", "n_scenes", "lst_offset_iqr_f")
ORIGINAL_FEATURES = (
    "dist_to_lake_km", "dist_to_loop_km", "median_year_structure_built", "pop_density_km2",
    "building_share", "green_share", "paved_use_share", "road_km_per_km2",
)
BASELINE_FEATURES = {
    "heat_offset_model": set(ORIGINAL_FEATURES),
    "exp_bt_phys": {"dist_to_lake_km", "dist_to_loop_km", "pop_density_km2", "median_year_structure_built"},
    "heat_offset_v2": {"latitude", "longitude", "dist_to_lake_km", "log_dist_to_lake", "dist_to_loop_km",
                       "log_dist_to_loop", "building_share", "green_share", "paved_use_share", "road_km_per_km2",
                       "log_pop_density", "building_year"},
    "heat_offset_v3": {"latitude", "longitude", "lat_lon_cross", "lat_sq", "lon_sq", "dist_to_lake_km",
                       "log_dist_to_lake", "sqrt_dist_to_lake", "dist_to_loop_km", "log_dist_to_loop",
                       "building_share", "green_share", "paved_use_share", "total_impervious",
                       "net_impervious_deficit", "road_km_per_km2", "log_roads", "log_pop_density", "building_year"},
}
MODEL_SCHEMA = [
    ("run_id", "STRING"), ("model_name", "STRING"), ("model_reference", "STRING"),
    ("role", "STRING"), ("feature_policy", "STRING"), ("evaluated_at", "TIMESTAMP"),
    ("evaluation_status", "STRING"), ("training_rows", "INTEGER"), ("holdout_rows", "INTEGER"),
    ("mae_f", "FLOAT64"), ("rmse_f", "FLOAT64"), ("r2", "FLOAT64"), ("mean_error_f", "FLOAT64"),
    ("rank_spearman_vs_original", "FLOAT64"), ("top20_overlap_vs_original", "INTEGER"),
    ("rank_comparison_rows", "INTEGER"), ("campaign_date", "DATE"), ("campaign_date_status", "STRING"),
    ("notes", "STRING"),
]
PREDICTION_SCHEMA = [
    ("run_id", "STRING"), ("model_name", "STRING"), ("cell_id", "STRING"),
    ("predicted_air_offset_f", "FLOAT64"), ("observed_air_offset_f", "FLOAT64"),
    ("n_obs", "INTEGER"), ("is_holdout", "BOOLEAN"), ("in_evaluation", "BOOLEAN"),
    ("role", "STRING"),
]
SCHEMAS = {"model_validation": MODEL_SCHEMA, "model_predictions": PREDICTION_SCHEMA}


def identifier(value: str, project: bool = False) -> str:
    pattern = r"[a-z][a-z0-9-]{4,61}[a-z0-9]" if project else r"[A-Za-z_][A-Za-z0-9_]{0,1023}"
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Invalid BigQuery identifier: {value}")
    return value


def candidate_features(include_lst: bool = False) -> tuple[str, ...]:
    return PHYSICAL_FEATURES + (LST_FEATURES if include_lst else ())


def validate_candidate_features(features: tuple[str, ...]) -> None:
    if set(features) - set(PHYSICAL_FEATURES + LST_FEATURES):
        raise ValueError("Candidate features must be explicitly allowed physical measurements")
    if not set(PHYSICAL_FEATURES) <= set(features):
        raise ValueError("Candidate must retain the agreed physical feature set")


def eligible(row: dict[str, Any], holdout: bool) -> bool:
    value = row.get("offset_f")
    return (row.get("is_holdout") is holdout and row.get("n_obs", 0) >= MIN_OBS
            and isinstance(value, (int, float)) and math.isfinite(value))


def lst_usable(row: dict[str, Any]) -> bool:
    offset = row.get("lst_offset_f")
    iqr = row.get("lst_offset_iqr_f")
    count = row.get("n_scenes")
    return (isinstance(count, (int, float)) and count >= 5
            and isinstance(offset, (int, float)) and math.isfinite(offset)
            and isinstance(iqr, (int, float)) and math.isfinite(iqr) and iqr >= 0)


def cohort_predicate(holdout: bool, alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    flag = "TRUE" if holdout else "FALSE"
    return (f"{prefix}n_obs >= {MIN_OBS} AND {prefix}is_holdout = {flag} "
            f"AND {prefix}offset_f IS NOT NULL AND NOT IS_NAN({prefix}offset_f) "
            f"AND NOT IS_INF({prefix}offset_f)")


def candidate_sql(project: str, work: str, model_name: str, include_lst: bool = False) -> str:
    for name in (work, model_name):
        identifier(name)
    identifier(project, project=True)
    features = candidate_features(include_lst)
    validate_candidate_features(features)
    transforms = [
        "ML.BUCKETIZE(dist_to_lake_km, [0.5, 1.0, 2.0, 4.0, 8.0, 14.0]) AS lake_distance_band",
        "ML.BUCKETIZE(dist_to_loop_km, [2.0, 4.0, 7.0, 11.0, 16.0]) AS downtown_distance_band",
        "ML.BUCKETIZE(median_year_structure_built, [1940, 1955, 1970, 1985]) AS building_era",
        "building_share", "green_share", "paved_use_share", "LOG(1 + road_km_per_km2) AS log_road_density",
        "tree_canopy_pct / 100.0 AS canopy_fraction", "impervious_pct / 100.0 AS impervious_fraction",
        "tree_canopy_pct_300m / 100.0 AS canopy_fraction_300m",
        "impervious_pct_300m / 100.0 AS impervious_fraction_300m",
    ]
    if include_lst:
        quality = ("n_scenes >= 5 AND lst_offset_f IS NOT NULL AND NOT IS_NAN(lst_offset_f) "
                   "AND NOT IS_INF(lst_offset_f) AND lst_offset_iqr_f IS NOT NULL "
                   "AND NOT IS_NAN(lst_offset_iqr_f) AND NOT IS_INF(lst_offset_iqr_f) AND lst_offset_iqr_f >= 0")
        transforms += [f"IF({quality}, lst_offset_f, NULL) AS lst_predictor_offset_f",
                       f"IF({quality}, lst_offset_iqr_f, NULL) AS lst_predictor_iqr_f",
                       f"IF({quality}, 0.0, 1.0) AS lst_unavailable_indicator"]
    transforms.append("offset_f")
    return (f"CREATE MODEL `{project}.{work}.{model_name}`\nTRANSFORM (\n  "
            + ",\n  ".join(transforms)
            + "\n)\nOPTIONS(model_type='LINEAR_REG', input_label_cols=['offset_f'], "
              "data_split_method='NO_SPLIT', l2_reg=1.0, enable_global_explain=TRUE) AS\nSELECT "
            + ", ".join([*features, "offset_f"])
            + f"\nFROM `{project}.{work}.site_context_enriched`\nWHERE {cohort_predicate(False)}")


def baseline_input(project: str, source: str, model: str) -> str:
    """Exact expressions recovered from original CREATE MODEL job metadata."""
    fields = ["cell_id", "offset_f", "n_obs", "is_holdout", *ORIGINAL_FEATURES]
    if model in {"heat_offset_v2", "heat_offset_v3"}:
        adjustment = "0.5" if model == "heat_offset_v2" else "0.2"
        fields += ["latitude", "longitude", f"LN(dist_to_lake_km + {adjustment}) AS log_dist_to_lake",
                   f"LN(dist_to_loop_km + {adjustment}) AS log_dist_to_loop",
                   "LN(pop_density_km2 + 1.0) AS log_pop_density",
                   "IF(median_year_structure_built > 1800, median_year_structure_built, 1950) AS building_year"]
    if model == "heat_offset_v3":
        fields += ["latitude * longitude AS lat_lon_cross", "POW(latitude - 41.8781, 2) AS lat_sq",
                   "POW(longitude - (-87.6298), 2) AS lon_sq", "POW(dist_to_lake_km, 0.5) AS sqrt_dist_to_lake",
                   "paved_use_share + building_share AS total_impervious",
                   "paved_use_share + building_share - green_share AS net_impervious_deficit",
                   "LN(road_km_per_km2 + 1.0) AS log_roads"]
    return "SELECT " + ", ".join(fields) + f" FROM `{project}.{source}.site_context`"


def prediction_sql(project: str, source: str, work: str, run_id: str,
                   candidates: tuple[str, ...]) -> str:
    for name in (source, work, run_id, *candidates):
        identifier(name)
    identifier(project, project=True)
    selects = []
    for model in (*BASELINE_MODELS, *candidates):
        role = "candidate" if model in candidates else "historical_baseline"
        dataset = work if role == "candidate" else source
        candidate_columns = ["cell_id", "offset_f", "n_obs", "is_holdout", *candidate_features(model.endswith("_lst"))]
        input_sql = (f"SELECT {', '.join(candidate_columns)} FROM `{project}.{work}.site_context_enriched`"
                     if role == "candidate" else baseline_input(project, source, model))
        selects.append(f"SELECT '{run_id}' AS run_id, '{model}' AS model_name, cell_id, "
                       f"predicted_offset_f AS predicted_air_offset_f, offset_f AS observed_air_offset_f, "
                       f"n_obs, is_holdout, ({cohort_predicate(True)}) AS in_evaluation, '{role}' AS role "
                       f"FROM ML.PREDICT(MODEL `{project}.{dataset}.{model}`, ({input_sql}))")
    selects.append(f"SELECT '{run_id}' AS run_id, 'predict_zero' AS model_name, cell_id, "
                   f"0.0 AS predicted_air_offset_f, offset_f AS observed_air_offset_f, n_obs, is_holdout, "
                   f"({cohort_predicate(True)}) AS in_evaluation, 'zero_baseline' AS role "
                   f"FROM `{project}.{work}.site_context_enriched`")
    return f"CREATE OR REPLACE TABLE `{project}.{work}.model_predictions` AS\n" + "\nUNION ALL\n".join(selects)


def preflight_sql(project: str, source: str, work: str) -> str:
    return f"""WITH enriched AS (SELECT * FROM `{project}.{work}.site_context_enriched`),
source AS (SELECT * FROM `{project}.{source}.grid_cells`)
SELECT (SELECT COUNT(*) FROM enriched) AS cells,
 (SELECT COUNT(DISTINCT cell_id) FROM enriched) AS unique_cells,
 (SELECT COUNTIF({cohort_predicate(False)}) FROM enriched) AS training_cells,
 (SELECT COUNTIF({cohort_predicate(True)}) FROM enriched) AS holdout_cells,
 (SELECT COUNTIF(tree_canopy_pct NOT BETWEEN 0 AND 100 OR impervious_pct NOT BETWEEN 0 AND 100
  OR tree_canopy_pct_300m NOT BETWEEN 0 AND 100 OR impervious_pct_300m NOT BETWEEN 0 AND 100) FROM enriched) AS invalid_land_cover_units,
 (SELECT COUNTIF(tree_canopy_pct IS NULL OR impervious_pct IS NULL OR tree_canopy_pct_300m IS NULL
  OR impervious_pct_300m IS NULL) FROM enriched) AS missing_land_cover,
 (SELECT COUNTIF(offset_f IS NOT NULL AND n_obs < {MIN_OBS}) FROM source) AS historical_low_observation_labels,
 (SELECT COUNTIF(offset_f IS NOT NULL AND NOT is_holdout) FROM source) AS historical_custom_training,
 (SELECT COUNT(*) FROM enriched e FULL OUTER JOIN source s USING(cell_id)
  WHERE e.cell_id IS NULL OR s.cell_id IS NULL OR e.offset_f IS DISTINCT FROM s.offset_f
  OR e.n_obs IS DISTINCT FROM s.n_obs OR e.is_holdout IS DISTINCT FROM s.is_holdout) AS identity_or_label_changes"""


def validate_preflight(row: dict[str, Any]) -> None:
    expected = {"cells": EXPECTED_CELLS, "unique_cells": EXPECTED_CELLS,
                "training_cells": EXPECTED_TRAIN, "holdout_cells": EXPECTED_HOLDOUT,
                "invalid_land_cover_units": 0, "missing_land_cover": 0, "identity_or_label_changes": 0}
    errors = {key: (row.get(key), value) for key, value in expected.items() if row.get(key) != value}
    if errors:
        raise ValueError(f"Enriched model cohort/feature validation failed: {errors}")


def regression_metrics(observed: list[float], predicted: list[float]) -> dict[str, float | None]:
    if not observed or len(observed) != len(predicted):
        raise ValueError("Metrics require equal nonempty observation/prediction cohorts")
    if any(not math.isfinite(value) for value in [*observed, *predicted]):
        raise ValueError("Metrics require finite observations and predictions")
    residuals = [prediction - observation for observation, prediction in zip(observed, predicted)]
    sse = sum(value * value for value in residuals)
    mean = statistics.fmean(observed)
    sst = sum((value - mean) ** 2 for value in observed)
    return {"mae_f": statistics.fmean(abs(value) for value in residuals),
            "rmse_f": math.sqrt(sse / len(observed)), "r2": 1 - sse / sst if sst > 0 else None,
            "mean_error_f": statistics.fmean(residuals)}


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        stop = start + 1
        while stop < len(order) and values[order[stop]] == values[order[start]]:
            stop += 1
        rank = (start + stop - 1) / 2 + 1
        for index in order[start:stop]:
            result[index] = rank
        start = stop
    return result


def ranking_stability(reference: dict[str, float], candidate: dict[str, float]) -> dict[str, Any]:
    if set(reference) != set(candidate) or not reference:
        raise ValueError("Ranking comparison requires identical nonempty cell sets")
    keys = sorted(reference)
    left = ranks([reference[key] for key in keys])
    right = ranks([candidate[key] for key in keys])
    if len(set(left)) < 2 or len(set(right)) < 2:
        return {"rank_spearman_vs_original": None, "top20_overlap_vs_original": None,
                "rank_comparison_rows": len(keys)}
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    covariance = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    scale = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
    left_top = set(sorted(keys, key=lambda key: (-reference[key], key))[:20])
    right_top = set(sorted(keys, key=lambda key: (-candidate[key], key))[:20])
    return {"rank_spearman_vs_original": covariance / scale,
            "top20_overlap_vs_original": len(left_top & right_top), "rank_comparison_rows": len(keys)}


def model_results(rows: list[dict[str, Any]], run_id: str, project: str, source: str,
                  work: str, candidates: tuple[str, ...], evaluated_at: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["model_name"]].append(row)
    expected_models = set(BASELINE_MODELS) | set(candidates) | {"predict_zero"}
    if set(groups) != expected_models:
        raise ValueError("Prediction table does not contain all agreed models")
    prediction_maps = {}
    for model, group in groups.items():
        if len(group) != EXPECTED_CELLS or len({row["cell_id"] for row in group}) != EXPECTED_CELLS:
            raise ValueError(f"{model} predictions must cover every cell once")
        if any(row["predicted_air_offset_f"] is None or not math.isfinite(row["predicted_air_offset_f"]) for row in group):
            raise ValueError(f"{model} has unavailable predictions; metrics will not silently drop cells")
        prediction_maps[model] = {row["cell_id"]: row["predicted_air_offset_f"] for row in group}
    reference = prediction_maps["heat_offset_model"]
    reference_holdout = {row["cell_id"]: row["observed_air_offset_f"] for row in groups["heat_offset_model"] if row["in_evaluation"]}
    results = []
    for model in (*BASELINE_MODELS, *candidates, "predict_zero"):
        group = groups[model]
        holdout = sorted([row for row in group if row["in_evaluation"]], key=lambda row: row["cell_id"])
        if len(holdout) != EXPECTED_HOLDOUT or {row["cell_id"]: row["observed_air_offset_f"] for row in holdout} != reference_holdout:
            raise ValueError(f"{model} holdout cell IDs or air labels differ")
        role = "candidate" if model in candidates else "zero_baseline" if model == "predict_zero" else "historical_baseline"
        policy = ("original physical + NLCD canopy/impervious; no demographic inputs" if role == "candidate"
                  else "constant air offset zero" if role == "zero_baseline"
                  else "historical physical-only schema" if model == "exp_bt_phys"
                  else "historical schema including population-density proxy")
        if model.endswith("_lst"):
            policy += "; quality-gated LST predictor and missingness, air-offset target"
        note = "Exploratory: multiple prior experiments reused this spatial holdout. Heat-only rankings across all city grid cells; no automatic promotion."
        if model in {"heat_offset_v2", "heat_offset_v3"}:
            note += " Historical training additionally included low-observation labeled cells; source job SQL used CUSTOM is_holdout split."
        if role == "candidate":
            note += " Numeric missing values use BigQuery ML training-mean imputation; unreliable LST is masked before imputation."
        metrics = regression_metrics([row["observed_air_offset_f"] for row in holdout], [row["predicted_air_offset_f"] for row in holdout])
        results.append({"run_id": run_id, "model_name": model,
                        "model_reference": None if model == "predict_zero" else f"{project}.{work if role == 'candidate' else source}.{model}",
                        "role": role, "feature_policy": policy, "evaluated_at": evaluated_at,
                        "evaluation_status": "exploratory_reused_spatial_holdout",
                        "training_rows": (None if role == "zero_baseline" else HISTORICAL_CUSTOM_TRAIN
                                          if model in {"heat_offset_v2", "heat_offset_v3"} else EXPECTED_TRAIN),
                        "holdout_rows": len(holdout), **metrics,
                        **ranking_stability(reference, prediction_maps[model]),
                        "campaign_date": None, "campaign_date_status": "unresolved_from_verified_source_metadata", "notes": note})
    return results


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str, allow_nan=False) + "\n")


def execute_plan(args: argparse.Namespace, statements: dict[str, str], candidates: tuple[str, ...]) -> None:
    from run_data_priorities import WorkRunner

    runner = WorkRunner(args.project, args.source_dataset, args.work_dataset)
    for priority in ("3", "4"):
        if runner.report["priorities"].get(priority, {}).get("status") != "validated":
            raise ValueError(f"Priority{priority} must be validated before training/scoring models")
    preflight = runner.query("model_candidate_preflight", statements["preflight"])[0]
    validate_preflight(preflight)
    # Metadata calls are read-only and reject unexpected historical feature schemas.
    for model in BASELINE_MODELS:
        metadata = runner.client.get_model(f"{args.project}.{args.source_dataset}.{model}")
        metadata_fields = metadata.to_api_repr()
        if {column["name"] for column in metadata_fields["labelColumns"]} != {"predicted_offset_f"}:
            raise ValueError(f"Unexpected air-offset label schema for {model}")
        if {column["name"] for column in metadata_fields["featureColumns"]} != BASELINE_FEATURES[model]:
            raise ValueError(f"Unexpected historical feature schema for {model}")
    for model in candidates:
        if args.score_only:
            metadata = runner.client.get_model(f"{args.project}.{args.work_dataset}.{model}")
            if {column["name"] for column in metadata.to_api_repr()["featureColumns"]} != set(candidate_features(model.endswith("_lst"))):
                raise ValueError(f"Existing {model} feature policy differs from this candidate")
        else:
            runner.query(f"train_{model}", statements[f"train_{model}"])
    runner.query("score_frozen_models", statements["predictions"])
    rows = runner.query("read_model_predictions", f"SELECT * FROM `{args.project}.{args.work_dataset}.model_predictions` ORDER BY model_name, cell_id")
    evaluated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    results = model_results(rows, args.run_id, args.project, args.source_dataset, args.work_dataset, candidates, evaluated_at)
    runner.load("model_validation", results, MODEL_SCHEMA)
    save_json(args.output_dir / "model_validation.json", {"preflight": preflight, "models": results})
    runner.complete("5", {"status": "exploratory comparison completed; no model promoted", "run_id": args.run_id,
                           "preflight": preflight, "models": results})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="qwiklabs-gcp-04-3e58e01275b8")
    parser.add_argument("--source-dataset", default="a4i_climate_backup_20261117_214035")
    parser.add_argument("--work-dataset", default="a4i_climate_work_v2")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/enrichment/model_evaluation")
    parser.add_argument("--run-id", default="enriched_physical_20261117")
    parser.add_argument("--include-lst", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Train and score only after validated priorities3/4")
    parser.add_argument("--score-only", action="store_true", help="With --execute, reuse already-created candidate models after feature-schema checks")
    args = parser.parse_args()
    identifier(args.project, project=True)
    for name in (args.source_dataset, args.work_dataset, args.run_id):
        identifier(name)
    if args.work_dataset == args.source_dataset or args.work_dataset == "a4i_climate" or "backup" in args.work_dataset:
        parser.error("Working dataset must be separate from originals and backups")
    if args.score_only and not args.execute:
        parser.error("--score-only requires --execute")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = ("heat_offset_candidate", "heat_offset_candidate_lst") if args.include_lst else ("heat_offset_candidate",)
    statements = {"preflight": preflight_sql(args.project, args.source_dataset, args.work_dataset),
                  "predictions": prediction_sql(args.project, args.source_dataset, args.work_dataset, args.run_id, candidates),
                  **{f"train_{model}": candidate_sql(args.project, args.work_dataset, model, model.endswith("_lst")) for model in candidates}}
    plan = {"execute": args.execute, "project": args.project, "source_dataset": args.source_dataset,
            "work_dataset": args.work_dataset, "run_id": args.run_id, "candidate_models": candidates,
            "baseline_models": BASELINE_MODELS, "required_validated_priorities": ["3", "4"],
            "maximum_bytes_billed_per_query": 5_000_000_000, "training_rows": EXPECTED_TRAIN,
            "holdout_rows": EXPECTED_HOLDOUT, "cells": EXPECTED_CELLS,
            "evaluation_status": "exploratory_reused_spatial_holdout", "automatic_promotion": False,
            "campaign_date": None, "campaign_date_status": "unresolved_from_verified_source_metadata",
            "feature_policy": "Candidates exclude demographics and population density; historical schemas retained for comparisons",
            "lst_policy": "At least5scenes, finite offset/nonnegative finite IQR; mask low-quality/missing values; train-mean impute with unavailable indicator",
            "sql_files": {}, "output_schemas": SCHEMAS}
    for name, sql in statements.items():
        path = args.output_dir / f"{name}.sql"
        path.write_text(sql + ";\n")
        plan["sql_files"][name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    save_json(args.output_dir / "plan.json", plan)
    print(json.dumps({"execute": args.execute, "candidate_models": candidates, "plan": str(args.output_dir / 'plan.json')}, indent=2))
    if args.execute:
        execute_plan(args, statements, candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
