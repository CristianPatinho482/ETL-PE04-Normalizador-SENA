"""Lectura y extracción de la tabla PE-04 desde XLSX/CSV.

Maneja cabeceras desplazadas, BOM, codificaciones Latin-1/UTF-8, delimitadores
y filas fantasma de Microsoft Office.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .contracts import ContractViolation, precondition
from .text import clean_text, is_blank_row

SUPPORTED_SUFFIXES = {".csv", ".xlsx", ".xlsm"}
GHOST_PREFIXES = ("~$",)
GHOST_SUFFIXES = (".tmp",)

HEADER_SIGNATURES = {
    "IDENTIFICADOR_FICHA",
    "CODIGO_PROGRAMA",
    "NOMBRE_PROGRAMA_FORMACION",
    "TOTAL_APRENDICES",
}
MIN_HEADER_SIGNATURES = 2
MAX_HEADER_ROWS = 5

REQUIRED_COLUMNS = (
    "IDENTIFICADOR_FICHA",
    "NOMBRE_PROGRAMA_FORMACION",
    "TOTAL_APRENDICES",
    "TOTAL_HORAS",
)

OPTIONAL_COLUMNS = (
    "IDENTIFICADOR_UNICO_FICHA",
    "CODIGO_PROGRAMA",
    "CODIGO_JORNADA",
    "NOMBRE_JORNADA",
    "TOTAL_APRENDICES_ACTIVOS",
    "TOTAL_APRENDICES_CANCELADOS",
    "TOTAL_APRENDICES_MASCULINOS",
    "TOTAL_APRENDICES_FEMENINOS",
    "TOTAL_APRENDICES_NOBINARIO",
    "IDPERIODO",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_header_name(value: Any) -> str:
    """Normaliza un nombre de columna: BOM, acentos, espacios y mayúsculas."""
    text = clean_text(value)
    if not text:
        return ""
    decomposed = re.sub(r"\s+", "_", text.strip())
    import unicodedata

    ascii_text = "".join(
        ch for ch in unicodedata.normalize("NFKD", decomposed) if not unicodedata.combining(ch)
    )
    return ascii_text.upper()


def _check_source(path: Path) -> None:
    precondition(path.exists(), f"El archivo fuente no existe: {path}")
    precondition(path.is_file(), f"La ruta fuente no es un archivo: {path}")
    name = path.name
    precondition(not name.startswith(GHOST_PREFIXES), f"Archivo temporal de Office no permitido: {name}")
    precondition(not name.lower().endswith(GHOST_SUFFIXES), f"Archivo .tmp no permitido: {name}")
    precondition(
        path.suffix.lower() in SUPPORTED_SUFFIXES,
        f"Extensión no soportada '{path.suffix}'. Use: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
    )


@dataclass(frozen=True)
class RawTable:
    frame: pd.DataFrame
    kind: str
    encoding: str | None
    delimiter: str | None
    sheet_name: str | None
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ExtractResult:
    frame: pd.DataFrame
    header_row: int
    warnings: list[str]
    blank_rows_skipped: int
    source_rows_nonblank: int
    columns: list[str]


def _read_excel(path: Path) -> RawTable:
    workbook = pd.ExcelFile(path, engine="openpyxl")
    sheet_name = workbook.sheet_names[0]
    frame = pd.read_excel(
        workbook,
        sheet_name=sheet_name,
        header=None,
        dtype=object,
    )
    return RawTable(
        frame=frame,
        kind="xlsx",
        encoding=None,
        delimiter=None,
        sheet_name=str(sheet_name),
        path=path,
        sha256=sha256_file(path),
        size_bytes=path.stat().st_size,
    )


def _detect_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            data.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _detect_delimiter(text: str) -> str:
    """Elige el delimitador de la primera línea con estructura tabular.

    Prioriza ';' porque es el delimitador oficial de Sofía Plus; los archivos
    con comas decimales no deben confundirse con CSV delimitados por coma.
    """
    for line in text.splitlines():
        if not line.strip():
            continue
        if ";" in line:
            return ";"
        if "\t" in line:
            return "\t"
        if "|" in line:
            return "|"
        if "," in line:
            return ","
    return ";"


def _read_csv(path: Path) -> RawTable:
    data = path.read_bytes()
    encoding = _detect_encoding(data)
    text = data.decode(encoding)
    delimiter = _detect_delimiter(text)

    candidates = [delimiter] + [d for d in (";", ",", "\t", "|") if d != delimiter]
    last_error: Exception | None = None
    frame = None
    for candidate in candidates:
        try:
            candidate_frame = pd.read_csv(
                io.StringIO(text),
                header=None,
                dtype=str,
                sep=candidate,
                keep_default_na=False,
                na_values=[""],
                engine="python",
            )
        except Exception as exc:  # noqa: BLE001  # pragma: no cover - defensivo
            last_error = exc
            continue
        if candidate_frame.shape[1] > 1 or candidate == candidates[-1]:
            delimiter = candidate
            frame = candidate_frame
            break
    if frame is None:
        raise ContractViolation("P", f"No se pudo leer el CSV: {last_error}")

    return RawTable(
        frame=frame,
        kind="csv",
        encoding=encoding,
        delimiter=delimiter,
        sheet_name=None,
        path=path,
        sha256=sha256_bytes(data),
        size_bytes=len(data),
    )


def read_raw_table(path: str | Path) -> RawTable:
    source = Path(path)
    _check_source(source)
    if source.suffix.lower() == ".csv":
        return _read_csv(source)
    return _read_excel(source)


def detect_header_row(frame: pd.DataFrame, max_rows: int = MAX_HEADER_ROWS) -> int:
    """Devuelve el índice 0-based de la cabecera o -1 si no la encuentra.

    Se exigen al menos dos firmas oficiales en la misma fila. Escanear solo
    las primeras ``max_rows`` filas evita confundir datos con encabezados.
    """
    limit = min(max_rows, len(frame))
    for index in range(limit):
        cells = {
            normalize_header_name(value)
            for value in frame.iloc[index].tolist()
            if clean_text(value)
        }
        if len(HEADER_SIGNATURES.intersection(cells)) >= MIN_HEADER_SIGNATURES:
            return index
    return -1


def extract_pe04(raw: RawTable, max_header_rows: int = MAX_HEADER_ROWS) -> ExtractResult:
    """Extrae la tabla PE-04 a partir de la fila de cabecera detectada."""
    header_row = detect_header_row(raw.frame, max_rows=max_header_rows)
    precondition(
        header_row >= 0,
        "Cabecera PE-04 no encontrada en las primeras "
        f"{max_header_rows} filas (firmas: {sorted(HEADER_SIGNATURES)}).",
    )

    header_values = raw.frame.iloc[header_row].tolist()
    columns: list[str] = []
    for position, value in enumerate(header_values, start=1):
        name = normalize_header_name(value)
        if not name:
            name = f"COLUMNA_SIN_NOMBRE_{position}"
        columns.append(name)

    duplicates = sorted({name for name in columns if columns.count(name) > 1})
    precondition(
        not duplicates,
        f"Columnas duplicadas tras normalizar cabecera: {duplicates}",
    )

    data = raw.frame.iloc[header_row + 1 :].copy()
    data.columns = columns
    data = data.reset_index(drop=True)

    blank_mask = data.apply(lambda row: is_blank_row(row.tolist()), axis=1)
    blank_rows_skipped = int(blank_mask.sum())
    data = data.loc[~blank_mask].reset_index(drop=True)

    missing = [column for column in REQUIRED_COLUMNS if column not in data.columns]
    precondition(
        not missing,
        f"Faltan columnas obligatorias de PE-04: {missing}. Columnas detectadas: {columns}",
    )

    warnings: list[str] = []
    if len(data) == 0:
        warnings.append("La tabla PE-04 no contiene filas de datos después de la cabecera.")
    if len(columns) < 47:
        warnings.append(
            f"La tabla tiene {len(columns)} columnas; la especificación menciona K>=47. "
            "Se procesan las columnas disponibles."
        )
    missing_optional = [column for column in OPTIONAL_COLUMNS if column not in data.columns]
    if missing_optional:
        warnings.append(f"Columnas opcionales ausentes: {missing_optional}")

    return ExtractResult(
        frame=data,
        header_row=header_row,
        warnings=warnings,
        blank_rows_skipped=blank_rows_skipped,
        source_rows_nonblank=len(data),
        columns=columns,
    )
