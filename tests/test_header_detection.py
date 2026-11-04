from __future__ import annotations

import pytest

from pe04_etl.contracts import ContractViolation
from pe04_etl.io import detect_header_row, extract_pe04, read_raw_table
from tests.conftest import BASE_COLUMNS, sample_row, write_csv, write_xlsx


@pytest.mark.parametrize("header_row", [0, 1, 2, 3, 4])
def test_detects_header_in_every_allowed_row(tmp_path, header_row):
    preamble = [
        "REPORTE PE-04 · EJECUCIÓN ACADÉMICA",
        "SERVICIO NACIONAL DE APRENDIZAJE · CENTRO ASTIN 9217",
        "Periodo de corte: 202605",
        "Documento generado por Sofía Plus",
    ][:header_row]
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row()],
        header_row=header_row,
        preamble=preamble,
    )
    raw = read_raw_table(path)
    result = extract_pe04(raw)
    assert result.header_row == header_row
    assert len(result.frame) == 1
    assert result.frame.iloc[0]["IDENTIFICADOR_FICHA"] == 3442618


def test_header_beyond_row_four_is_rejected(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row()],
        header_row=5,
        preamble=["Metadato 1", "Metadato 2", "Metadato 3", "Metadato 4", "Metadato 5"],
    )
    raw = read_raw_table(path)
    with pytest.raises(ContractViolation) as error:
        extract_pe04(raw)
    assert error.value.clause == "P"
    assert "Cabecera PE-04" in error.value.message


def test_preamble_with_single_signature_does_not_false_positive(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row()],
        header_row=3,
        preamble=["IDENTIFICADOR_FICHA", "Centro ASTIN", "Periodo 202605"],
    )
    raw = read_raw_table(path)
    assert detect_header_row(raw.frame) == 3
    assert extract_pe04(raw).header_row == 3


def test_bom_and_accented_headers_are_normalized(tmp_path):
    columns = list(BASE_COLUMNS)
    columns[0] = "\ufeffIDENTIFICADOR_FICHA"
    columns[1] = "NOMBRE_PROGRAMA_FORMACIÓN"
    path = write_csv(tmp_path / "PE-04_mayo 2026.csv", [sample_row()], columns=columns)
    raw = read_raw_table(path)
    result = extract_pe04(raw)
    assert "IDENTIFICADOR_FICHA" in result.frame.columns
    assert "NOMBRE_PROGRAMA_FORMACION" in result.frame.columns
    assert result.frame.iloc[0]["NOMBRE_PROGRAMA_FORMACION"].startswith("APLICACION")


def test_duplicate_columns_are_rejected(tmp_path):
    columns = list(BASE_COLUMNS) + ["IDENTIFICADOR_FICHA"]
    path = write_xlsx(tmp_path / "PE-04_mayo 2026.xlsx", [sample_row()], columns=columns)
    raw = read_raw_table(path)
    with pytest.raises(ContractViolation) as error:
        extract_pe04(raw)
    assert "duplicadas" in error.value.message.lower()


def test_blank_rows_are_skipped_and_counted(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row(), {}, sample_row(IDENTIFICADOR_FICHA=3442577)],
    )
    raw = read_raw_table(path)
    result = extract_pe04(raw)
    assert len(result.frame) == 2
    assert result.blank_rows_skipped == 1


def test_missing_required_column_is_rejected(tmp_path):
    columns = [column for column in BASE_COLUMNS if column != "TOTAL_HORAS"]
    path = write_xlsx(tmp_path / "PE-04_mayo 2026.xlsx", [sample_row()], columns=columns)
    raw = read_raw_table(path)
    with pytest.raises(ContractViolation) as error:
        extract_pe04(raw)
    assert "TOTAL_HORAS" in error.value.message


def test_latin1_csv_is_read_correctly(tmp_path):
    path = write_csv(
        tmp_path / "PE-04_mayo 2026.csv",
        [sample_row(NOMBRE_PROGRAMA_FORMACION="GESTIÓN AMBIENTAL")],
        encoding="cp1252",
    )
    raw = read_raw_table(path)
    result = extract_pe04(raw)
    assert result.frame.iloc[0]["NOMBRE_PROGRAMA_FORMACION"] == "GESTIÓN AMBIENTAL"
