from __future__ import annotations

from pe04_etl.pipeline import run_pipeline
from pe04_etl.quality import build_quality_report
from tests.conftest import sample_row, write_csv, write_xlsx


def test_composite_cell_precedes_column_and_flags_inconsistency(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [
            sample_row(
                NOMBRE_PROGRAMA_FORMACION="3442618_BIOTECNOLOGIA_DIURNA",
                IDENTIFICADOR_FICHA=3449999,
            )
        ],
    )
    result = run_pipeline(path)
    row = result.curated.iloc[0]
    assert row["CODIGO_FICHA"] == 3442618
    assert row["NOMBRE_PROGRAMA"] == "BIOTECNOLOGIA"
    assert row["CODIGO_JORNADA"] == 1
    assert row["ESTADO_REGISTRO"] == "REVISAR"
    assert "FICHA_COMPUESTA_INCONSISTENTE" in set(result.audit["REGLA"])


def test_malformed_composite_cell_is_rejected_not_silently_split(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [
            sample_row(),
            sample_row(
                IDENTIFICADOR_FICHA=3500000,
                NOMBRE_PROGRAMA_FORMACION="3442618_PROGRAMA_SIN_JORNADA",
            ),
        ],
    )
    result = run_pipeline(path)
    assert len(result.curated) == 1
    assert len(result.rejected) == 1
    assert "CELDA_COMPUESTA_INVALIDA" in set(result.audit["REGLA"])
    assert "CELDA_COMPUESTA_INVALIDA" in result.rejected.iloc[0]["MOTIVO_RECHAZO"]


def test_invalid_metrics_are_zeroed_and_audited(tmp_path):
    invalid = sample_row(IDENTIFICADOR_FICHA=3500000)
    invalid["TOTAL_APRENDICES"] = "ABC"
    invalid["TOTAL_APRENDICES_ACTIVOS"] = "XYZ"
    invalid["TOTAL_APRENDICES_MASCULINOS"] = "NO"
    invalid["TOTAL_HORAS"] = "ABC"
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row(), invalid],
    )
    result = run_pipeline(path)
    row = result.curated[result.curated["CODIGO_FICHA"] == 3500000].iloc[0]
    assert row["APRENDICES_MATRICULADOS"] == 0
    assert row["APRENDICES_ACTIVOS"] == 0
    assert row["TOTAL_HORAS"] == 0.0
    assert row["ESTADO_REGISTRO"] == "REVISAR"
    rules = set(result.audit["REGLA"])
    assert {"METRICA_NO_NUMERICA", "HORAS_NO_NUMERICAS", "TOTAL_SEXO_NO_NUMERICO"} <= rules


def test_hours_out_of_decimal_range_are_zeroed(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [
            sample_row(),
            sample_row(IDENTIFICADOR_FICHA=3500000, TOTAL_HORAS="100000000"),
        ],
    )
    result = run_pipeline(path)
    row = result.curated[result.curated["CODIGO_FICHA"] == 3500000].iloc[0]
    assert row["TOTAL_HORAS"] == 0.0
    assert "HORAS_FUERA_DE_RANGO" in set(result.audit["REGLA"])


def test_period_missing_without_filename_hint_is_rejected(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04.xlsx",
        [
            sample_row(),
            sample_row(IDENTIFICADOR_FICHA=3500000, IDPERIODO=""),
        ],
    )
    result = run_pipeline(path)
    assert len(result.curated) == 1
    assert len(result.rejected) == 1
    assert "PERIODO_INVALIDO" in result.rejected.iloc[0]["MOTIVO_RECHAZO"]


def test_comma_delimited_csv_is_supported(tmp_path):
    path = write_csv(
        tmp_path / "PE-04_mayo 2026.csv",
        [sample_row(TOTAL_HORAS="116.56")],
        delimiter=",",
    )
    result = run_pipeline(path)
    assert len(result.curated) == 1
    assert result.metadata.delimiter == ","


def test_dq17_detects_same_ficha_with_two_programs(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[1, "CODIGO_FICHA"] = curated.loc[0, "CODIGO_FICHA"]
    curated.loc[1, "NOMBRE_PROGRAMA"] = "OTRO PROGRAMA"
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    outcomes = {item.rule_id: item for item in report.outcomes}
    assert outcomes["DQ-17"].status == "FAIL"
    assert outcomes["DQ-02"].status == "FAIL"


def test_dq14_detects_tampered_aggregate(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "APRENDICES_MATRICULADOS"] -= 1
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    outcomes = {item.rule_id: item for item in report.outcomes}
    assert outcomes["DQ-14"].status == "FAIL"


def test_dq23_detects_invalid_status(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "ESTADO_REGISTRO"] = "PENDIENTE"
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    assert {item.rule_id: item for item in report.outcomes}["DQ-23"].status == "WARN"


def test_dq22_detects_period_filename_mismatch(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    metadata = result.metadata.to_dict()
    metadata["period"] = 202606
    report = build_quality_report(
        result.curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        metadata,
    )
    assert {item.rule_id: item for item in report.outcomes}["DQ-22"].status == "WARN"
