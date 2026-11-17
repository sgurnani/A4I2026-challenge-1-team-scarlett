import math

import pytest

import evaluate_enriched_model as model


def test_training_sql_retains_frozen_split_and_excludes_demographic_inputs():
    sql = model.candidate_sql("example-project", "a4i_climate_work_v2", "heat_offset_candidate")
    assert "n_obs >= 8 AND is_holdout = FALSE" in sql
    assert "data_split_method='NO_SPLIT'" in sql
    assert "input_label_cols=['offset_f']" in sql
    assert "pop_density" not in sql
    assert "vulnerability" not in sql
    assert "poverty" not in sql
    assert "SELECT *" not in sql
    assert "tree_canopy_pct_300m / 100.0" in sql
    assert "CREATE MODEL `example-project.a4i_climate_work_v2.heat_offset_candidate`" in sql
    with pytest.raises(ValueError, match="explicitly allowed physical"):
        model.validate_candidate_features(model.PHYSICAL_FEATURES + ("poverty_rate",))


def test_no_sparse_labels_or_holdout_cells_enter_training_cohort():
    row = {"n_obs": 8, "is_holdout": False, "offset_f": 2.0}
    assert model.eligible(row, False)
    assert not model.eligible({**row, "n_obs": 7}, False)
    assert not model.eligible({**row, "is_holdout": True}, False)
    assert not model.eligible({**row, "offset_f": float("nan")}, False)
    assert not model.eligible({**row, "offset_f": None}, False)


def test_lst_missing_and_low_scene_coverage_are_masked_without_dropping_cells():
    good = {"n_scenes": 5, "lst_offset_f": 4.0, "lst_offset_iqr_f": 1.5}
    assert model.lst_usable(good)
    assert not model.lst_usable({**good, "n_scenes": 4})
    assert not model.lst_usable({**good, "lst_offset_f": None})
    assert not model.lst_usable({**good, "lst_offset_iqr_f": -1.0})
    sql = model.candidate_sql("example-project", "a4i_climate_work_v2", "heat_offset_candidate_lst", True)
    assert "lst_unavailable_indicator" in sql
    assert "lst_predictor_iqr_f" in sql
    where = sql.split("\nWHERE ")[1]
    assert "lst" not in where


def test_zero_offset_baseline_r_squared_is_computed_against_holdout_mean():
    metrics = model.regression_metrics([1.0, 3.0], [0.0, 0.0])
    assert metrics["r2"] == -4.0
    assert metrics["mae_f"] == 2.0
    assert metrics["rmse_f"] == math.sqrt(5)
    assert model.regression_metrics([2.0, 2.0], [1.0, 1.0])["r2"] is None
    with pytest.raises(ValueError, match="finite"):
        model.regression_metrics([1.0], [float("nan")])


def test_tied_ranks_and_constant_baseline_have_honest_stability_metrics():
    reference = {"a": 1.0, "b": 1.0, "c": 3.0}
    assert model.ranks([1.0, 1.0, 3.0]) == [1.5, 1.5, 3.0]
    assert model.ranking_stability(reference, reference)["rank_spearman_vs_original"] == 1
    constant = model.ranking_stability(reference, {"a": 0.0, "b": 0.0, "c": 0.0})
    assert constant["rank_spearman_vs_original"] is None
    assert constant["top20_overlap_vs_original"] is None
    with pytest.raises(ValueError, match="identical"):
        model.ranking_stability(reference, {"a": 1.0})


def test_baseline_engineering_matches_recovered_job_sql_and_recovery_inputs():
    v2 = model.baseline_input("example-project", "a4i_climate_backup_20261117_214035", "heat_offset_v2")
    v3 = model.baseline_input("example-project", "a4i_climate_backup_20261117_214035", "heat_offset_v3")
    assert "LN(dist_to_lake_km + 0.5)" in v2
    assert "LN(dist_to_lake_km + 0.2)" in v3
    assert "POW(latitude - 41.8781, 2)" in v3
    assert "median_year_structure_built > 1800" in v3
    assert "a4i_climate_backup_20261117_214035.site_context" in v2


def test_preflight_rejects_label_changes_missing_cells_and_units():
    valid = {"cells": 5053, "unique_cells": 5053, "training_cells": 1207, "holdout_cells": 442,
             "invalid_land_cover_units": 0, "missing_land_cover": 0, "identity_or_label_changes": 0}
    model.validate_preflight(valid)
    for key in ["unique_cells", "holdout_cells", "identity_or_label_changes", "invalid_land_cover_units"]:
        with pytest.raises(ValueError, match="cohort/feature validation"):
            model.validate_preflight({**valid, key: valid[key] + 1})


def prediction_rows():
    return [{"cell_id": key, "model_name": name, "predicted_air_offset_f": prediction,
             "observed_air_offset_f": label, "in_evaluation": holdout}
            for name in ["heat_offset_model", "heat_offset_candidate", "predict_zero"]
            for key, prediction, label, holdout in [("a", 1.0, 2.0, True), ("b", 2.0, 3.0, True), ("c", 3.0, None, False)]]


def test_comparison_rejects_missing_predictions_and_changed_holdout_labels(monkeypatch):
    monkeypatch.setattr(model, "EXPECTED_CELLS", 3)
    monkeypatch.setattr(model, "EXPECTED_HOLDOUT", 2)
    monkeypatch.setattr(model, "BASELINE_MODELS", ("heat_offset_model",))
    args = ("run_test", "example-project", "backup", "work", ("heat_offset_candidate",), "2026-11-17T00:00:00Z")
    results = model.model_results(prediction_rows(), *args)
    assert all(row["evaluation_status"] == "exploratory_reused_spatial_holdout" for row in results)
    damaged = prediction_rows()
    damaged[3]["predicted_air_offset_f"] = None
    with pytest.raises(ValueError, match="unavailable predictions"):
        model.model_results(damaged, *args)
    changed = prediction_rows()
    changed[3]["observed_air_offset_f"] = 2.1
    with pytest.raises(ValueError, match="air labels differ"):
        model.model_results(changed, *args)
