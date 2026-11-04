from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from pe04_etl.pipeline import run_pipeline
from pe04_etl.quality import build_quality_report


def _report(result):
    return build_quality_report(
        result.curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )


def test_clean_pipeline_has_no_critical_failures(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    report = _report(result)
    assert report.critical_failures == 0
    assert report.score == 100.0
    assert report.global_status == "APTO"
    assert all(outcome.status in {"PASS", "NO_EVALUABLE"} for outcome in report.outcomes)
    assert len(report.outcomes) >= 20


def test_duplicate_ficha_is_a_critical_failure(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    duplicated = pd.concat([result.curated, result.curated.iloc[[0]]], ignore_index=True)
    report = build_quality_report(
        duplicated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    outcome = {item.rule_id: item for item in report.outcomes}["DQ-02"]
    assert outcome.status == "FAIL"
    assert report.critical_failures >= 1
    assert report.global_status == "NO APTO"


def test_negative_metric_is_detected(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "APRENDICES_ACTIVOS"] = -1
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    assert {item.rule_id: item for item in report.outcomes}["DQ-07"].status == "FAIL"


def test_activos_mayor_que_matriculados_is_detected(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "APRENDICES_ACTIVOS"] = curated.loc[0, "APRENDICES_MATRICULADOS"] + 1
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    assert {item.rule_id: item for item in report.outcomes}["DQ-08"].status == "FAIL"


def test_invalid_period_is_detected(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "ID_PERIODO"] = 202613
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    assert {item.rule_id: item for item in report.outcomes}["DQ-04"].status == "FAIL"


def test_residual_composite_and_dirty_text_are_detected(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "NOMBRE_PROGRAMA"] = " 1234567_PROGRAMA_DIURNA "
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    outcomes = {item.rule_id: item for item in report.outcomes}
    assert outcomes["DQ-11"].status == "FAIL"
    assert outcomes["DQ-12"].status == "WARN"


def test_html_escapes_untrusted_program_names(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "NOMBRE_PROGRAMA"] = "<script>alert(1)</script>"
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    document = report.render_html()
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert "Tablero de Calidad de Datos" in document
    assert "DQ-01" in document


def test_report_is_json_serializable(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    report = _report(result)
    payload = json.dumps(report.to_dict(), ensure_ascii=False)
    assert "DQ-23" in payload
    assert "score" in payload


def test_artifact_integrity_detects_missing_sidecar(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    sidecar = Path(result.paths["curated"]).with_name("pe04_curated.csv.sha256")
    sidecar.unlink()
    report = _report(result)
    assert {item.rule_id: item for item in report.outcomes}["DQ-19"].status == "WARN"


def test_review_rows_must_have_audit_coverage(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    curated = result.curated.copy()
    curated.loc[0, "ESTADO_REGISTRO"] = "REVISAR"
    report = build_quality_report(
        curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    assert {item.rule_id: item for item in report.outcomes}["DQ-20"].status == "WARN"
