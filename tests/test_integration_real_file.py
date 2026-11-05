from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pe04_etl.normalize import parse_hours, parse_integer
from pe04_etl.pipeline import run_pipeline
from pe04_etl.quality import build_quality_report

pytestmark = pytest.mark.integration

EXPECTED_ROWS = 453
EXPECTED_PERIOD = 202605
EXPECTED_ROUNDED = 109


def test_real_pe04_file_end_to_end(real_source: Path, tmp_path):
    result = run_pipeline(real_source, tmp_path / "curated")
    curated = result.curated

    assert result.metadata.header_row == 0
    assert len(curated) == EXPECTED_ROWS
    assert curated["CODIGO_FICHA"].is_unique
    assert curated["ID_REGISTRO_UUID"].is_unique
    assert curated["ID_PERIODO"].nunique() == 1
    assert int(curated["ID_PERIODO"].iloc[0]) == EXPECTED_PERIOD
    assert curated["CODIGO_JORNADA"].isin([1, 2, 3, 4]).all()
    assert (curated["TOTAL_HORAS"] >= 0).all()
    assert ((curated["TOTAL_HORAS"] - curated["TOTAL_HORAS"].round(2)).abs() < 1e-9).all()
    assert curated["NOMBRE_PROGRAMA"].map(lambda value: value.strip() == value).all()
    assert result.metadata.rows_rounded == EXPECTED_ROUNDED


def test_real_pe04_sums_reconcile_with_source(real_source: Path, tmp_path):
    result = run_pipeline(real_source, tmp_path / "curated")
    source = result.extract.frame
    curated = result.curated

    source_mat = sum(parse_integer(value)[0] or 0 for value in source["TOTAL_APRENDICES"])
    source_act = sum(
        parse_integer(value)[0] or 0 for value in source["TOTAL_APRENDICES_ACTIVOS"]
    )
    source_hours = sum(parse_hours(value)[0] or 0 for value in source["TOTAL_HORAS"])

    assert int(curated["APRENDICES_MATRICULADOS"].sum()) == source_mat
    assert int(curated["APRENDICES_ACTIVOS"].sum()) == source_act
    assert abs(float(curated["TOTAL_HORAS"].sum()) - float(source_hours)) <= 0.01 * len(source)


def test_real_pe04_rerun_is_byte_identical(real_source: Path, tmp_path):
    first = run_pipeline(real_source, tmp_path / "run_a")
    second = run_pipeline(real_source, tmp_path / "run_b")
    assert Path(first.paths["curated"]).read_bytes() == Path(second.paths["curated"]).read_bytes()
    assert first.metadata.curated_sha256 == second.metadata.curated_sha256


def test_real_pe04_quality_report_is_clean(real_source: Path, tmp_path):
    result = run_pipeline(real_source, tmp_path / "curated")
    report = build_quality_report(
        result.curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    assert report.critical_failures == 0
    assert report.global_status == "APTO"
    assert report.score == 100.0
    assert report.row_conformity == 100.0
    assert all(
        outcome.status in {"PASS", "NO_EVALUABLE"} for outcome in report.outcomes
    )
    html = report.render_html()
    assert "202605" in html
    assert "453" in html


def test_real_pe04_csv_shape_and_header(real_source: Path, tmp_path):
    result = run_pipeline(real_source, tmp_path / "curated")
    frame = pd.read_csv(
        result.paths["curated"],
        sep=";",
        encoding="utf-8-sig",
        dtype=str,
    )
    assert len(frame) == EXPECTED_ROWS
    assert list(frame.columns) == [
        "ID_REGISTRO_UUID",
        "ID_PERIODO",
        "CODIGO_FICHA",
        "NOMBRE_PROGRAMA",
        "CODIGO_JORNADA",
        "APRENDICES_MATRICULADOS",
        "APRENDICES_ACTIVOS",
        "APRENDICES_CANCELADOS",
        "TOTAL_HORAS",
        "ESTADO_REGISTRO",
    ]
    assert frame["TOTAL_HORAS"].str.fullmatch(r"\d+\.\d{2}").all()
