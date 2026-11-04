from __future__ import annotations

import pytest

from pe04_etl.contracts import ContractViolation
from pe04_etl.normalize import CURATED_COLUMNS, normalize_dataframe
from pe04_etl.pipeline import run_pipeline
from tests.conftest import sample_row, write_xlsx


def test_missing_source_violates_precondition(tmp_path):
    with pytest.raises(ContractViolation) as error:
        run_pipeline(tmp_path / "no_existe.xlsx")
    assert error.value.clause == "P"
    assert "no existe" in error.value.message


def test_ghost_office_file_is_rejected(tmp_path):
    ghost = tmp_path / "~$PE-04_mayo 2026.xlsx"
    ghost.write_bytes(b"temporal")
    with pytest.raises(ContractViolation) as error:
        run_pipeline(ghost)
    assert error.value.clause == "P"
    assert "temporal" in error.value.message.lower()


def test_unsupported_extension_is_rejected(tmp_path):
    path = tmp_path / "PE-04_mayo 2026.txt"
    path.write_text("no es una fuente válida", encoding="utf-8")
    with pytest.raises(ContractViolation) as error:
        run_pipeline(path)
    assert error.value.clause == "P"
    assert "Extensión" in error.value.message


def test_empty_table_violates_precondition(tmp_path):
    path = write_xlsx(tmp_path / "PE-04_mayo 2026.xlsx", [])
    with pytest.raises(ContractViolation) as error:
        run_pipeline(path)
    assert error.value.clause == "P"
    assert "no contiene filas" in error.value.message


def test_invalid_rows_are_rejected_and_audited(tmp_path):
    rows = [
        sample_row(),
        sample_row(IDENTIFICADOR_FICHA="ABC"),
        sample_row(NOMBRE_PROGRAMA_FORMACION=""),
        sample_row(),  # duplicado exacto de la clave natural
        sample_row(IDENTIFICADOR_FICHA=3500000, TOTAL_HORAS="-5"),
        sample_row(
            IDENTIFICADOR_FICHA=3500001,
            CODIGO_JORNADA=99,
            NOMBRE_JORNADA="DESCONOCIDA",
        ),
        sample_row(IDENTIFICADOR_FICHA=3500002, TOTAL_APRENDICES_ACTIVOS=30),
    ]
    path = write_xlsx(tmp_path / "PE-04_mayo 2026.xlsx", rows)
    result = run_pipeline(path)

    assert len(result.curated) == 4
    assert len(result.rejected) == 3
    assert len(result.audit) >= 5
    assert result.metadata.rows_accepted == 4
    assert result.metadata.rows_rejected == 3
    assert result.metadata.rows_in_review == 3  # horas negativas, jornada y activos
    assert set(result.rejected["MOTIVO_RECHAZO"].str.contains("FICHA_INVALIDA|PROGRAMA_VACIO|CLAVE_NATURAL_DUPLICADA"))
    assert "CLAVE_NATURAL_DUPLICADA" in set(result.audit["REGLA"])
    assert "HORAS_NEGATIVAS" in set(result.audit["REGLA"])
    assert "JORNADA_DESCONOCIDA" in set(result.audit["REGLA"])
    assert "ACTIVOS_MAYOR_QUE_MATRICULADOS" in set(result.audit["REGLA"])
    assert (result.curated["ESTADO_REGISTRO"] == "REVISAR").sum() == 3


def test_all_invalid_rows_violate_postcondition(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row(IDENTIFICADOR_FICHA="ABC")],
    )
    with pytest.raises(ContractViolation) as error:
        run_pipeline(path)
    assert error.value.clause == "Q"
    assert "no puede estar vacío" in error.value.message


def test_postconditions_hold_on_clean_source(source_two_rows):
    result = run_pipeline(source_two_rows)
    curated = result.curated
    assert list(curated.columns) == CURATED_COLUMNS
    assert curated["ID_REGISTRO_UUID"].is_unique
    assert (curated["CODIGO_FICHA"] > 0).all()
    assert curated["CODIGO_JORNADA"].isin([1, 2, 3, 4]).all()
    assert (curated["TOTAL_HORAS"] >= 0).all()
    assert ((curated["TOTAL_HORAS"] - curated["TOTAL_HORAS"].round(2)).abs() < 1e-9).all()
    assert curated["ESTADO_REGISTRO"].isin(["OK", "REVISAR"]).all()
    assert len(result.audit) == 0


def test_cancelados_absent_defaults_to_zero_and_is_traced(source_two_rows):
    result = run_pipeline(source_two_rows)
    assert (result.curated["APRENDICES_CANCELADOS"] == 0).all()
    assert result.metadata.cancelados_disponible is False
    assert any("CANCELADOS" in warning for warning in result.metadata.warnings)


def test_postcondition_rejects_multiple_periods(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [
            sample_row(),
            sample_row(IDENTIFICADOR_FICHA=3500000, IDPERIODO=202606),
        ],
    )
    with pytest.raises(ContractViolation) as error:
        run_pipeline(path)
    assert error.value.clause == "Q"
    assert "único periodo" in error.value.message


def test_normalize_dataframe_direct_call_requires_dataframe():
    with pytest.raises(ContractViolation) as error:
        normalize_dataframe([{"IDENTIFICADOR_FICHA": 1}])  # type: ignore[arg-type]
    assert error.value.clause == "P"
