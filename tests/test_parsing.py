from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from pe04_etl.normalize import (
    clean_program_name,
    map_jornada,
    parse_composite,
    parse_decimal,
    parse_hours,
    parse_integer,
    parse_period,
    period_from_filename,
    period_from_preamble,
    quantize_hours,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("116,5666667", Decimal("116.5666667")),
        ("\xa0116,56 ", Decimal("116.56")),
        ("1.234,56", Decimal("1234.56")),
        ("1,234.56", Decimal("1234.56")),
        ("1.234", Decimal("1.234")),
        ("1.234.567", Decimal(1234567)),
        ("(1.234,56)", Decimal("-1234.56")),
        ("-40,5", Decimal("-40.5")),
        ("100.00", Decimal("100.00")),
    ],
)
def test_parse_decimal_localized_formats(raw, expected):
    value, error = parse_decimal(raw)
    assert error is None
    assert value == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "\xa0", "abc", "N/A", "$"])
def test_parse_decimal_marks_invalid_values(raw):
    value, error = parse_decimal(raw)
    assert value is None
    assert error is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.234", 1234),
        ("1,234", 1234),
        ("3.469.952", 3469952),
        ("26.0", 26),
        (" 26 ", 26),
        (202605.0, 202605),
        ("-5", -5),
    ],
)
def test_parse_integer_accepts_thousands_and_integral_floats(raw, expected):
    value, error = parse_integer(raw)
    assert error is None
    assert value == expected


@pytest.mark.parametrize("raw", ["116,56", "abc", "", None])
def test_parse_integer_rejects_non_integers(raw):
    value, error = parse_integer(raw)
    assert value is None
    assert error is not None


def test_quantize_hours_half_up_to_two_decimals():
    value, error, rounded = quantize_hours("1,005")
    assert error is None
    assert value == Decimal("1.01")
    assert rounded is True

    value, error, rounded = quantize_hours("116,5666667")
    assert error is None
    assert value == Decimal("116.57")
    assert rounded is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20", Decimal(20)),
        ("20,5", Decimal("20.5")),
        ("20:00", Decimal(20)),
        ("24:00", Decimal(24)),
        ("144:00", Decimal(144)),
        ("1899-12-31T20:00:00.000", Decimal(20)),
        ("1900-01-01T00:00:00.000", Decimal(24)),
        ("1900-01-06T00:00:00.000", Decimal(144)),
        ("1899-12-31T00:00:00.000", Decimal(0)),
        (20.5, Decimal("20.5")),
        (time(20, 0), Decimal(20)),
        (timedelta(hours=144), Decimal(144)),
        (datetime(1899, 12, 31, 20, 0), Decimal(20)),
    ],
)
def test_parse_hours_accepts_excel_and_csv_durations(raw, expected):
    value, error = parse_hours(raw)
    assert error is None
    assert value == expected


def test_parse_hours_converts_minutes_not_centesimal_hours():
    value, error = parse_hours("39:50")
    assert error is None
    assert value is not None
    assert abs(value - Decimal("39.8333333")) < Decimal("0.0001")
    assert quantize_hours("39:50") == (Decimal("39.83"), None, True)


@pytest.mark.parametrize(
    "raw",
    ["", None, "   ", "abc", "20:75", "20:61", "2026-06", datetime(2026, 6, 1)],
)
def test_parse_hours_rejects_invalid_or_non_duration_values(raw):
    value, error = parse_hours(raw)
    assert value is None
    assert error is not None


def test_composite_cell_is_split_into_atoms():
    result = parse_composite("  3442618_BIOTECNOLOGIA_DIURNA  ")
    assert result.matched is True
    assert result.ficha == 3442618
    assert result.programa == "BIOTECNOLOGIA"
    assert result.codigo_jornada == 1


def test_composite_cell_accepts_underscores_inside_program():
    result = parse_composite("3442618_APLICACION_DE_PROCESOS_MIXTA")
    assert result.matched is True
    assert result.programa == "APLICACION_DE_PROCESOS"
    assert result.codigo_jornada == 4


def test_composite_cell_accepts_nbsp_and_lowercase():
    result = parse_composite("3442618\xa0_\xa0Biotecnología\xa0_\xa0Nocturna")
    assert result.matched is True
    assert result.programa == "BIOTECNOLOGÍA"
    assert result.codigo_jornada == 2


@pytest.mark.parametrize(
    "raw",
    [
        "3442618_BIOTECNOLOGIA",
        "3442618__DIURNA",
        "ABC_BIOTECNOLOGIA_DIURNA",
    ],
)
def test_malformed_composite_cells_are_flagged(raw):
    result = parse_composite(raw)
    assert result.matched is False
    assert result.reason in {"NO_COMPUESTO", "FORMATO_COMPUESTO_INVALIDO"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1, 1),
        ("1", 1),
        ("DIURNA", 1),
        ("diurno", 1),
        ("NOCTURNA", 2),
        ("MADRUGADA", 3),
        ("MIXTA", 4),
        ("mixto", 4),
        (4.0, 4),
        ("99", None),
        ("TARDE", None),
    ],
)
def test_map_jornada(raw, expected):
    assert map_jornada(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("202605", 202605),
        (202605, 202605),
        ("2026-05", 202605),
        ("05/2026", 202605),
        (202605.0, 202605),
        ("202613", None),
        ("189901", None),
        ("", None),
    ],
)
def test_parse_period(raw, expected):
    assert parse_period(raw) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("PE-04_mayo 2026.xlsx", 202605),
        ("PE-04_202605.csv", 202605),
        ("PE-04_JUN 2026.xml", 202606),
        ("PE-04_ENERO 2026.csv", 202601),
        ("PE-04_SEP 2026.xlsx", 202609),
        ("PE-04_DIC 2026.csv", 202612),
        ("reporte_202612.xlsx", 202612),
        ("PE-04_sin_periodo.xlsx", None),
    ],
)
def test_period_from_filename(filename, expected):
    assert period_from_filename(filename) == expected


def test_period_from_preamble_reads_period_before_header():
    frame = pd.DataFrame([["PE-04_"], [None], ["202606"], ["CODIGO_REGIONAL"]])
    assert period_from_preamble(frame, max_rows=3) == 202606


def test_period_from_preamble_ignores_fichas_and_empty_values():
    frame = pd.DataFrame([["PE-04_"], ["3533390"], [None]])
    assert period_from_preamble(frame, max_rows=3) is None


def test_clean_program_name_removes_numeric_prefix_and_collapses_spaces():
    assert clean_program_name(" 1234567 -  Gestión   Ambiental ") == "GESTIÓN AMBIENTAL"
    assert clean_program_name("1234567_PROGRAMA_X") == "PROGRAMA_X"
