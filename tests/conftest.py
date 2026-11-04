from __future__ import annotations

import csv
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASE_COLUMNS = [
    "IDENTIFICADOR_FICHA",
    "NOMBRE_PROGRAMA_FORMACION",
    "NOMBRE_JORNADA",
    "CODIGO_JORNADA",
    "TOTAL_APRENDICES_MASCULINOS",
    "TOTAL_APRENDICES_FEMENINOS",
    "TOTAL_APRENDICES_NOBINARIO",
    "TOTAL_APRENDICES",
    "TOTAL_APRENDICES_ACTIVOS",
    "TOTAL_HORAS",
    "IDPERIODO",
]

PROGRAM = "APLICACION DE PROCESOS DE BIOTECNOLOGIA Y AUTOMATIZACION"


def sample_row(**overrides) -> dict:
    """Fila PE-04 mínima y consistente para pruebas."""
    mat = int(overrides.pop("TOTAL_APRENDICES", 26))
    act = int(overrides.pop("TOTAL_APRENDICES_ACTIVOS", mat))
    masculinos = int(overrides.pop("TOTAL_APRENDICES_MASCULINOS", mat))
    femeninos = int(overrides.pop("TOTAL_APRENDICES_FEMENINOS", 0))
    no_binario = int(overrides.pop("TOTAL_APRENDICES_NOBINARIO", 0))
    row = {
        "IDENTIFICADOR_FICHA": 3442618,
        "NOMBRE_PROGRAMA_FORMACION": PROGRAM,
        "NOMBRE_JORNADA": "DIURNA",
        "CODIGO_JORNADA": 1,
        "TOTAL_APRENDICES_MASCULINOS": masculinos,
        "TOTAL_APRENDICES_FEMENINOS": femeninos,
        "TOTAL_APRENDICES_NOBINARIO": no_binario,
        "TOTAL_APRENDICES": mat,
        "TOTAL_APRENDICES_ACTIVOS": act,
        "TOTAL_HORAS": "116,5666667",
        "IDPERIODO": 202605,
    }
    row.update(overrides)
    return row


def _matrix(rows: list[dict], columns: list[str], header_row: int, preamble) -> list[list]:
    header = list(columns)
    if preamble is None:
        preamble_rows = [[""] * len(columns) for _ in range(header_row)]
    else:
        preamble_rows = []
        for item in preamble:
            if isinstance(item, str):
                item = [item]
            preamble_rows.append(list(item) + [""] * (len(columns) - len(item)))
        while len(preamble_rows) < header_row:
            preamble_rows.append([""] * len(columns))
        preamble_rows = preamble_rows[:header_row]
    from pe04_etl.io import normalize_header_name

    body = []
    for row in rows:
        lookup = {normalize_header_name(key): value for key, value in row.items()}
        body.append([lookup.get(normalize_header_name(column), "") for column in columns])
    return preamble_rows + [header] + body


def write_xlsx(
    path: Path,
    rows: list[dict],
    *,
    header_row: int = 0,
    preamble=None,
    columns: list[str] | None = None,
) -> Path:
    columns = list(columns or BASE_COLUMNS)
    matrix = _matrix(rows, columns, header_row, preamble)
    pd.DataFrame(matrix).to_excel(path, header=False, index=False, engine="openpyxl")
    return path


def write_csv(
    path: Path,
    rows: list[dict],
    *,
    header_row: int = 0,
    preamble=None,
    columns: list[str] | None = None,
    encoding: str = "utf-8-sig",
    delimiter: str = ";",
) -> Path:
    columns = list(columns or BASE_COLUMNS)
    matrix = _matrix(rows, columns, header_row, preamble)
    with open(path, "w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator="\n")
        writer.writerows(matrix)
    return path


@pytest.fixture
def source_two_rows(tmp_path: Path) -> Path:
    path = tmp_path / "PE-04_mayo 2026.xlsx"
    rows = [
        sample_row(),
        sample_row(
            IDENTIFICADOR_FICHA=3442577,
            TOTAL_APRENDICES=25,
            TOTAL_APRENDICES_ACTIVOS=25,
            TOTAL_APRENDICES_MASCULINOS=10,
            TOTAL_APRENDICES_FEMENINOS=14,
            TOTAL_APRENDICES_NOBINARIO=1,
            TOTAL_HORAS="117,0833333",
        ),
    ]
    return write_xlsx(path, rows)


@pytest.fixture(scope="session")
def real_source() -> Path:
    path = PROJECT_ROOT / "PE-04_mayo 2026.xlsx"
    if not path.exists():
        pytest.skip(f"No está disponible el archivo real: {path}")
    return path
