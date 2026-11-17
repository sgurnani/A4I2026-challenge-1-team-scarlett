"""Protect originals and recovery snapshots from enrichment-runner writes."""
import pytest

from run_data_priorities import identifier, validate_targets


@pytest.mark.parametrize("sql", [
    "CREATE OR REPLACE TABLE `project-id.a4i_climate.grid_cells` AS SELECT 1",
    "CREATE MODEL `project-id.a4i_climate_backup_20261117.model` OPTIONS() AS SELECT 1",
    "INSERT INTO `project-id.a4i_climate.high_schools` SELECT 1",
    "CREATE TABLE IF NOT EXISTS `project-id.a4i_climate.facility_readiness` (id STRING)",
    "DROP TABLE `project-id.a4i_climate_work_v2.example`",
])
def test_original_and_backup_writes_are_rejected(sql):
    with pytest.raises(ValueError):
        validate_targets(sql, "project-id", "a4i_climate_work_v2")


def test_working_output_can_read_original_and_backup_inputs():
    validate_targets("CREATE OR REPLACE TABLE `project-id.a4i_climate_work_v2.cell_tract_map` "
                     "AS SELECT * FROM `project-id.a4i_climate.grid_cells`", "project-id", "a4i_climate_work_v2")


def test_sql_identifiers_cannot_contain_statement_delimiters():
    with pytest.raises(ValueError):
        identifier("dataset`; DROP TABLE sensitive;")
