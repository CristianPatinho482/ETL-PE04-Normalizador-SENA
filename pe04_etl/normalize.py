"""Normalización 1FN y contratos de la transformación PE-04."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import pandas as pd

from .contracts import ProcessingMetadata, postcondition, precondition
from .text import clean_text, is_missing, normalize_for_match

CURATED_COLUMNS = [
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

AUDIT_COLUMNS = [
    "ID_REGISTRO_UUID",
    "ID_PERIODO",
    "CODIGO_FICHA",
    "FILA_ORIGEN",
    "REGLA",
    "SEVERIDAD",
    "CAMPO",
    "VALOR_CRUDO",
    "DETALLE",
]

JORNADA_POR_NOMBRE = {
    "DIURNA": 1,
    "DIURNO": 1,
    "NOCTURNA": 2,
    "NOCTURNO": 2,
    "MADRUGADA": 3,
    "MIXTA": 4,
    "MIXTO": 4,
}

MESES_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Los nombres de archivo de Sofía Plus usan abreviaturas de tres letras
# (por ejemplo, ``PE-04_JUN 2026.xml``). Se aceptan también variantes en inglés.
MESES_ABREVIADOS = {
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "set": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
    "jan": 1,
    "apr": 4,
    "aug": 8,
    "dec": 12,
}

MAX_HORAS = Decimal("99999999.99")
CENTAVO = Decimal("0.01")

# Excel representa duraciones como fracciones de día con epoch 1899-12-31.
# Sofía Plus exporta TOTAL_HORAS así en XLSX y XML SpreadsheetML, y como
# ``HH:MM`` en CSV; ninguna de esas formas es un decimal convencional.
EXCEL_EPOCH = datetime(1899, 12, 31)
_DURATION_CLOCK_RE = re.compile(
    r"^(?P<hours>\d{1,7}):(?P<minutes>\d{1,2})(?::(?P<seconds>\d{1,2}(?:[.,]\d+)?))?$"
)
_DURATION_ISO_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?:[T ](?P<clock>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?))?$"
)

# UUID v5 determinista: misma clave natural => mismo identificador.
# La especificación menciona UUID v4, pero v4 es aleatorio y rompe f(f(x))=f(x).
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://sena.edu.co/etl/pe04/v1")

_COMPOSITE_RE = re.compile(
    r"^(?P<ficha>\d{6,10})\s*_\s*(?P<programa>.+?)\s*_\s*"
    r"(?P<jornada>NOCTURNA|MADRUGADA|DIURNA|MIXTA)\s*$",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(r"^(?P<year>\d{4})[-/]?(?P<month>0[1-9]|1[0-2])$")
_PERIOD_REVERSED_RE = re.compile(r"^(?P<month>0[1-9]|1[0-2])[-/](?P<year>\d{4})$")
_RESIDUAL_COMPOSITE_RE = re.compile(
    r"^\d{6,10}\s*_.+_(NOCTURNA|MADRUGADA|DIURNA|MIXTA)\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class CompositeCell:
    matched: bool
    raw: str
    ficha: int | None = None
    programa: str | None = None
    codigo_jornada: int | None = None
    reason: str | None = None


@dataclass
class NormalizationResult:
    curated: pd.DataFrame
    audit: pd.DataFrame
    rejected: pd.DataFrame
    metadata: ProcessingMetadata


def make_record_uuid(
    period: int,
    ficha: int,
    programa: str,
    codigo_jornada: int,
) -> str:
    """Clave subrogada determinista (UUID v5) a partir de la clave natural."""
    natural_key = f"{period}|{ficha}|{clean_text(programa).upper()}|{codigo_jornada}"
    return str(uuid.uuid5(UUID_NAMESPACE, natural_key))


def parse_decimal(value: Any) -> tuple[Decimal | None, str | None]:
    """Convierte un valor localizado (coma decimal, miles con punto) a Decimal.

    Reglas:
      * ``116,5667`` -> 116.5667
      * ``1.234,56`` -> 1234.56
      * ``1,234.56`` -> 1234.56
      * ``1.234``    -> 1.234 (en contexto decimal)
      * ``1.234.567`` -> 1234567
      * ``(1.234,56)`` -> -1234.56
    """
    text = clean_text(value)
    if not text:
        return None, "VACIO"

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()

    text = text.replace(" ", "")
    text = re.sub(r"[^\d.,+\-]", "", text)
    if not re.search(r"\d", text):
        return None, "NO_NUMERICO"

    if text.count("-") > 1 or ("-" in text and not text.startswith("-")):
        return None, "NO_NUMERICO"
    if text.count("+") > 1 or ("+" in text and not text.startswith("+")):
        return None, "NO_NUMERICO"

    negative = negative or text.startswith("-")
    text = text.lstrip("+-")
    if not text:
        return None, "NO_NUMERICO"

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if text.count(",") > 1:
            parts = text.split(",")
            if all(len(part) == 3 for part in parts[1:]):
                text = "".join(parts)
            else:
                text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = text.replace(",", ".")
    elif "." in text and text.count(".") > 1:
        parts = text.split(".")
        if all(len(part) == 3 for part in parts[1:]):
            text = "".join(parts)
        else:
            text = "".join(parts[:-1]) + "." + parts[-1]

    try:
        result = Decimal(text)
    except InvalidOperation:
        return None, "NO_NUMERICO"
    if not result.is_finite():
        return None, "NO_NUMERICO"
    if negative:
        result = -result
    return result, None


def parse_integer(value: Any) -> tuple[int | None, str | None]:
    """Convierte a entero aceptando miles con punto o coma.

    Para conteos, ``1.234`` se interpreta como 1234 (formato colombiano),
    no como 1.234.
    """
    text = clean_text(value)
    if not text:
        return None, "VACIO"

    if re.fullmatch(r"[+-]?\d{1,3}(\.\d{3})+", text):
        text = text.replace(".", "")
    elif re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+", text):
        text = text.replace(",", "")

    number, error = parse_decimal(text)
    if error:
        return None, error
    assert number is not None
    if number != number.to_integral_value():
        return None, "NO_ENTERO"
    return int(number), None


def _seconds_to_hours(seconds: float) -> Decimal:
    """Convierte segundos a horas sin introducir binario flotante adicional."""
    return Decimal(str(seconds)) / Decimal(3600)


def parse_hours(value: Any) -> tuple[Decimal | None, str | None]:
    """Convierte duraciones de Excel/CSV a horas decimales.

    Formas aceptadas:
      * ``20`` o ``20,5`` -> horas decimales directas.
      * ``20:00`` -> 20 horas; ``144:00`` -> 144 horas.
      * ``39:50`` -> 39.8333... horas (horas y minutos, no centésimas).
      * ``1899-12-31T20:00:00.000`` -> 20 horas.
      * ``1900-01-06T00:00:00.000`` -> 144 horas.
      * ``datetime.time`` y ``timedelta`` provenientes de openpyxl.
    """
    if is_missing(value):
        return None, "VACIO"
    if isinstance(value, bool):
        return None, "NO_NUMERICO"

    if isinstance(value, timedelta):
        return _seconds_to_hours(value.total_seconds()), None

    if isinstance(value, datetime):
        if value.year >= 2000:
            return None, "FECHA_NO_DURACION"
        seconds = (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        )
        days = (value.date() - EXCEL_EPOCH.date()).days
        if days < 0:
            return None, "DURACION_NEGATIVA"
        return Decimal(days) * 24 + _seconds_to_hours(seconds), None

    if isinstance(value, date):
        if value.year >= 2000:
            return None, "FECHA_NO_DURACION"
        days = (value - EXCEL_EPOCH.date()).days
        if days < 0:
            return None, "DURACION_NEGATIVA"
        return Decimal(days) * 24, None

    if isinstance(value, time):
        seconds = (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        )
        return _seconds_to_hours(seconds), None

    text = clean_text(value).replace(" ", "")
    if not text:
        return None, "VACIO"

    clock_match = _DURATION_CLOCK_RE.fullmatch(text)
    if clock_match:
        hours = int(clock_match.group("hours"))
        minutes = int(clock_match.group("minutes"))
        seconds_text = clock_match.group("seconds") or "0"
        seconds = Decimal(seconds_text.replace(",", "."))
        if minutes >= 60 or seconds >= 60:
            return None, "NO_NUMERICO"
        return Decimal(hours) + Decimal(minutes) / 60 + seconds / 3600, None

    iso_match = _DURATION_ISO_RE.fullmatch(text)
    if iso_match:
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None, "NO_NUMERICO"
        return parse_hours(moment)

    if re.match(r"^\d{4}-\d{2}", text):
        return None, "FECHA_NO_DURACION"

    number, error = parse_decimal(text)
    if error:
        return None, error
    return number, None


def quantize_hours(value: Any) -> tuple[Decimal | None, str | None, bool]:
    """Cuantiza a 2 decimales (ROUND_HALF_UP) y reporta si hubo cambio."""
    number, error = parse_hours(value)
    if error:
        return None, error, False
    assert number is not None
    quantized = number.quantize(CENTAVO, rounding=ROUND_HALF_UP)
    return quantized, None, quantized != number


def map_jornada(value: Any) -> int | None:
    """Mapea código o nombre de jornada al dominio {1,2,3,4}."""
    if is_missing(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            code = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return code if code in {1, 2, 3, 4} else None

    text = normalize_for_match(value)
    if not text:
        return None
    if text.isdigit():
        code = int(text)
        return code if code in {1, 2, 3, 4} else None
    return JORNADA_POR_NOMBRE.get(text)


def parse_period(value: Any) -> int | None:
    """Valida y convierte un periodo YYYYMM (o YYYY-MM, MM/YYYY)."""
    text = clean_text(value).replace(" ", "")
    if not text:
        return None
    if re.fullmatch(r"\d{6}\.0+", text):
        text = text.split(".")[0]

    match = _PERIOD_RE.fullmatch(text)
    if match:
        year, month = int(match.group("year")), int(match.group("month"))
    else:
        match = _PERIOD_REVERSED_RE.fullmatch(text)
        if not match:
            return None
        year, month = int(match.group("year")), int(match.group("month"))

    if not (2000 <= year <= 2100):
        return None
    if not (1 <= month <= 12):
        return None
    return year * 100 + month


def period_from_filename(filename: str) -> int | None:
    """Deriva el periodo del nombre ``PE-04_mayo 2026.xlsx``."""
    text = normalize_for_match(filename).lower().replace("-", " ").replace("_", " ")
    match = re.search(r"(?<!\d)(20\d{2})\s*(0[1-9]|1[0-2])(?!\d)", text)
    if match:
        return int(match.group(1)) * 100 + int(match.group(2))

    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", text)
    if match:
        year = int(match.group(1))
        for month_name, month in MESES_ES.items():
            if re.search(rf"\b{month_name}\b", text):
                return year * 100 + month
        for month_name, month in MESES_ABREVIADOS.items():
            if re.search(rf"\b{month_name}\b", text):
                return year * 100 + month
        match_six = re.search(rf"(?<!\d){year}(0[1-9]|1[0-2])(?!\d)", text)
        if match_six:
            return year * 100 + int(match_six.group(1))
    return None


def period_from_preamble(frame: pd.DataFrame, max_rows: int = 5) -> int | None:
    """Busca un periodo YYYYMM en las filas de preámbulo del reporte.

    Algunos XML de Excel guardan el periodo en una celda suelta antes de la
    cabecera y no incluyen la columna ``IDPERIODO``. Esta función recupera ese
    dato sin confundirlo con fichas u otros identificadores numéricos.
    """
    limit = min(max_rows, len(frame))
    for index in range(limit):
        for value in frame.iloc[index].tolist():
            period = parse_period(value)
            if period is not None:
                return period
    return None


def parse_composite(value: Any) -> CompositeCell:
    """Descompone ``[Ficha]_[Programa]_[Jornada]`` de forma segura.

    A diferencia de la regex mínima de la especificación, admite guiones bajos
    dentro del nombre del programa porque el ancla final en la jornada evita
    particiones ambiguas.
    """
    raw = clean_text(value)
    if not raw:
        return CompositeCell(False, raw, reason="VACIO")

    match = _COMPOSITE_RE.fullmatch(raw)
    if not match:
        looks_composite = bool(re.match(r"^\d{5,10}\s*_", raw)) or bool(
            re.search(r"_(NOCTURNA|MADRUGADA|DIURNA|MIXTA)\s*$", raw, re.IGNORECASE)
        )
        reason = "FORMATO_COMPUESTO_INVALIDO" if looks_composite else "NO_COMPUESTO"
        return CompositeCell(False, raw, reason=reason)

    ficha, error = parse_integer(match.group("ficha"))
    programa = clean_text(match.group("programa")).upper()
    jornada = map_jornada(match.group("jornada"))
    if error or ficha is None or not programa or jornada is None:
        return CompositeCell(False, raw, reason="FORMATO_COMPUESTO_INVALIDO")
    return CompositeCell(
        True,
        raw,
        ficha=ficha,
        programa=programa,
        codigo_jornada=jornada,
    )


def clean_program_name(value: Any) -> str:
    """Limpia prefijos numéricos y normaliza el nombre del programa."""
    text = clean_text(value)
    text = re.sub(r"^\d{6,10}\s*[-_]\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.upper()


def _issue(
    issues: list[dict[str, Any]],
    *,
    rule: str,
    severity: str,
    field_name: str,
    raw_value: Any,
    detail: str,
) -> None:
    issues.append(
        {
            "REGLA": rule,
            "SEVERIDAD": severity,
            "CAMPO": field_name,
            "VALOR_CRUDO": clean_text(raw_value)[:500],
            "DETALLE": detail,
        }
    )


def _parse_metric(
    row: pd.Series,
    column: str,
    *,
    label: str,
    required: bool,
    issues: list[dict[str, Any]],
) -> int:
    raw = row.get(column)
    value, error = parse_integer(raw)
    if error:
        if error == "VACIO" and not required:
            return 0
        _issue(
            issues,
            rule="METRICA_NO_NUMERICA" if error != "VACIO" else "METRICA_VACIA",
            severity="Alta",
            field_name=column,
            raw_value=raw,
            detail=f"{label} no es un entero válido ({error}); se asigna 0.00 según la especificación.",
        )
        return 0
    assert value is not None
    if value < 0:
        _issue(
            issues,
            rule="METRICA_NEGATIVA",
            severity="Alta",
            field_name=column,
            raw_value=raw,
            detail=f"{label} es negativo ({value}); se asigna 0 por dominio >= 0.",
        )
        return 0
    return value


def normalize_dataframe(
    frame: pd.DataFrame,
    *,
    metadata: ProcessingMetadata | None = None,
    period_hint: int | None = None,
    period_source_hint: str | None = None,
    first_data_row: int = 1,
) -> NormalizationResult:
    """Transforma la tabla cruda PE-04 en el esquema 1FN objetivo."""
    precondition(isinstance(frame, pd.DataFrame), "La entrada debe ser un DataFrame.")
    precondition(not frame.empty, "La tabla PE-04 no contiene filas para normalizar.")
    if period_hint is not None:
        precondition(parse_period(period_hint) is not None, f"Periodo explícito inválido: {period_hint}")

    meta = metadata or ProcessingMetadata()
    meta.columns_detected = list(frame.columns)
    meta.required_columns_present = all(
        column in frame.columns
        for column in (
            "IDENTIFICADOR_FICHA",
            "NOMBRE_PROGRAMA_FORMACION",
            "TOTAL_APRENDICES",
            "TOTAL_HORAS",
        )
    )
    meta.activos_disponible = "TOTAL_APRENDICES_ACTIVOS" in frame.columns
    meta.cancelados_disponible = "TOTAL_APRENDICES_CANCELADOS" in frame.columns

    curated_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    seen_uuids: dict[str, int] = {}
    rounded_count = 0
    period_was_derived = False

    for position, (_, row) in enumerate(frame.iterrows()):
        source_row = first_data_row + position
        issues: list[dict[str, Any]] = []
        record_uuid = ""
        period: int | None = None
        ficha: int | None = None
        programa = ""
        jornada: int | None = None

        # --- Identidad: celda compuesta o columnas atómicas ---
        composite = parse_composite(row.get("NOMBRE_PROGRAMA_FORMACION"))
        if composite.matched:
            ficha = composite.ficha
            programa = composite.programa or ""
            jornada = composite.codigo_jornada
            raw_ficha = row.get("IDENTIFICADOR_FICHA")
            if not is_missing(raw_ficha):
                explicit_ficha, explicit_error = parse_integer(raw_ficha)
                if explicit_error is None and explicit_ficha is not None and explicit_ficha != ficha:
                    _issue(
                        issues,
                        rule="FICHA_COMPUESTA_INCONSISTENTE",
                        severity="Media",
                        field_name="IDENTIFICADOR_FICHA",
                        raw_value=raw_ficha,
                        detail=(
                            f"La ficha de la columna ({explicit_ficha}) difiere de la "
                            f"ficha embebida en la celda compuesta ({ficha}); prevalece la compuesta."
                        ),
                    )
        else:
            if composite.reason == "FORMATO_COMPUESTO_INVALIDO":
                _issue(
                    issues,
                    rule="CELDA_COMPUESTA_INVALIDA",
                    severity="Alta",
                    field_name="NOMBRE_PROGRAMA_FORMACION",
                    raw_value=composite.raw,
                    detail=(
                        "La celda parece compuesta pero no cumple "
                        "[Ficha]_[Programa]_[Jornada]; se rechaza la fila."
                    ),
                )
            ficha, ficha_error = parse_integer(row.get("IDENTIFICADOR_FICHA"))
            if ficha_error:
                _issue(
                    issues,
                    rule="FICHA_INVALIDA",
                    severity="Crítica",
                    field_name="IDENTIFICADOR_FICHA",
                    raw_value=row.get("IDENTIFICADOR_FICHA"),
                    detail=f"No se pudo interpretar la ficha: {ficha_error}.",
                )
            programa = clean_program_name(row.get("NOMBRE_PROGRAMA_FORMACION"))
            if not programa:
                _issue(
                    issues,
                    rule="PROGRAMA_VACIO",
                    severity="Crítica",
                    field_name="NOMBRE_PROGRAMA_FORMACION",
                    raw_value=row.get("NOMBRE_PROGRAMA_FORMACION"),
                    detail="El nombre del programa es obligatorio (NOT NULL).",
                )
            jornada = map_jornada(row.get("CODIGO_JORNADA"))
            if jornada is None:
                jornada = map_jornada(row.get("NOMBRE_JORNADA"))
            if jornada is None:
                jornada = 4
                _issue(
                    issues,
                    rule="JORNADA_DESCONOCIDA",
                    severity="Media",
                    field_name="CODIGO_JORNADA/NOMBRE_JORNADA",
                    raw_value=f"{row.get('CODIGO_JORNADA')} / {row.get('NOMBRE_JORNADA')}",
                    detail="Jornada no reconocida; se asigna 4 (MIXTA) y se marca REVISAR.",
                )

        if ficha is None or ficha <= 0:
            _issue(
                issues,
                rule="FICHA_FUERA_DE_RANGO",
                severity="Crítica",
                field_name="CODIGO_FICHA",
                raw_value=ficha,
                detail="La ficha debe ser un entero positivo.",
            )
        elif not (100_000 <= ficha <= 99_999_999):
            _issue(
                issues,
                rule="FICHA_FUERA_DE_RANGO",
                severity="Alta",
                field_name="CODIGO_FICHA",
                raw_value=ficha,
                detail="La ficha está fuera del dominio operativo 100000..99999999.",
            )

        # --- Periodo ---
        period = parse_period(row.get("IDPERIODO"))
        if period is None:
            if period_hint is not None:
                period = period_hint
                period_was_derived = True
            else:
                _issue(
                    issues,
                    rule="PERIODO_INVALIDO",
                    severity="Crítica",
                    field_name="IDPERIODO",
                    raw_value=row.get("IDPERIODO"),
                    detail="Periodo ausente o inválido; no hay periodo derivable del nombre del archivo.",
                )
        elif period_hint is not None and period != period_hint:
            _issue(
                issues,
                rule="PERIODO_INCONSISTENTE",
                severity="Alta",
                field_name="IDPERIODO",
                raw_value=row.get("IDPERIODO"),
                detail=f"El periodo de la fila ({period}) difiere del periodo del archivo ({period_hint}).",
            )

        # --- Métricas ---
        matriculados = _parse_metric(
            row,
            "TOTAL_APRENDICES",
            label="Aprendices matriculados",
            required=True,
            issues=issues,
        )
        activos = _parse_metric(
            row,
            "TOTAL_APRENDICES_ACTIVOS",
            label="Aprendices activos",
            required=False,
            issues=issues,
        )
        cancelados = _parse_metric(
            row,
            "TOTAL_APRENDICES_CANCELADOS",
            label="Aprendices cancelados",
            required=False,
            issues=issues,
        )
        if activos > matriculados:
            _issue(
                issues,
                rule="ACTIVOS_MAYOR_QUE_MATRICULADOS",
                severity="Alta",
                field_name="TOTAL_APRENDICES_ACTIVOS",
                raw_value=activos,
                detail=f"Activos ({activos}) supera matriculados ({matriculados}).",
            )

        sex_columns = (
            "TOTAL_APRENDICES_MASCULINOS",
            "TOTAL_APRENDICES_FEMENINOS",
            "TOTAL_APRENDICES_NOBINARIO",
        )
        if all(column in frame.columns for column in sex_columns):
            sex_values: list[int] = []
            sex_valid = True
            for column in sex_columns:
                value, error = parse_integer(row.get(column))
                if error and error != "VACIO":
                    sex_valid = False
                    _issue(
                        issues,
                        rule="TOTAL_SEXO_NO_NUMERICO",
                        severity="Media",
                        field_name=column,
                        raw_value=row.get(column),
                        detail=f"Conteo por sexo no numérico ({error}).",
                    )
                sex_values.append(value or 0)
            if sex_valid and sum(sex_values) != matriculados:
                _issue(
                    issues,
                    rule="TOTAL_SEXO_INCONSISTENTE",
                    severity="Media",
                    field_name="TOTAL_APRENDICES_MASCULINOS/FEMENINOS/NOBINARIO",
                    raw_value=sum(sex_values),
                    detail=(
                        f"La suma por sexo ({sum(sex_values)}) no coincide con "
                        f"TOTAL_APRENDICES ({matriculados})."
                    ),
                )

        # --- Horas ---
        horas, horas_error, rounded = quantize_hours(row.get("TOTAL_HORAS"))
        if horas_error:
            _issue(
                issues,
                rule="HORAS_NO_NUMERICAS" if horas_error != "VACIO" else "HORAS_VACIAS",
                severity="Alta",
                field_name="TOTAL_HORAS",
                raw_value=row.get("TOTAL_HORAS"),
                detail=f"TOTAL_HORAS no es numérico ({horas_error}); se asigna 0.00.",
            )
            horas = Decimal("0.00")
        elif horas is not None and horas < 0:
            _issue(
                issues,
                rule="HORAS_NEGATIVAS",
                severity="Alta",
                field_name="TOTAL_HORAS",
                raw_value=row.get("TOTAL_HORAS"),
                detail=f"TOTAL_HORAS es negativo ({horas}); se asigna 0.00.",
            )
            horas = Decimal("0.00")
        elif horas is not None and horas > MAX_HORAS:
            _issue(
                issues,
                rule="HORAS_FUERA_DE_RANGO",
                severity="Alta",
                field_name="TOTAL_HORAS",
                raw_value=row.get("TOTAL_HORAS"),
                detail=f"TOTAL_HORAS excede Decimal(10,2): {horas}.",
            )
            horas = Decimal("0.00")
        elif rounded:
            rounded_count += 1

        if horas is None:
            horas = Decimal("0.00")

        # --- UUID determinista y detección de duplicados ---
        if period is not None and ficha is not None and ficha > 0 and programa and jornada is not None:
            record_uuid = make_record_uuid(period, ficha, programa, jornada)

        if record_uuid:
            if record_uuid in seen_uuids:
                _issue(
                    issues,
                    rule="CLAVE_NATURAL_DUPLICADA",
                    severity="Crítica",
                    field_name="ID_REGISTRO_UUID",
                    raw_value=record_uuid,
                    detail=(
                        "La clave natural (periodo, ficha, programa, jornada) ya existe "
                        f"en la fila de origen {seen_uuids[record_uuid]}; se rechaza el duplicado."
                    ),
                )
            else:
                seen_uuids[record_uuid] = source_row

        blocking_rules = {
            "FICHA_INVALIDA",
            "FICHA_FUERA_DE_RANGO",
            "PROGRAMA_VACIO",
            "CELDA_COMPUESTA_INVALIDA",
            "PERIODO_INVALIDO",
            "CLAVE_NATURAL_DUPLICADA",
        }
        blocked = any(issue["REGLA"] in blocking_rules for issue in issues)

        if blocked:
            rejected = {str(column): row.get(column) for column in frame.columns}
            rejected["FILA_ORIGEN"] = source_row
            rejected["MOTIVO_RECHAZO"] = "; ".join(
                sorted({f"{issue['REGLA']}: {issue['DETALLE']}" for issue in issues})
            )
            rejected_rows.append(rejected)
        else:
            status = "REVISAR" if issues else "OK"
            curated_rows.append(
                {
                    "ID_REGISTRO_UUID": record_uuid,
                    "ID_PERIODO": int(period) if period is not None else 0,
                    "CODIGO_FICHA": int(ficha) if ficha is not None else 0,
                    "NOMBRE_PROGRAMA": programa,
                    "CODIGO_JORNADA": int(jornada) if jornada is not None else 4,
                    "APRENDICES_MATRICULADOS": int(matriculados),
                    "APRENDICES_ACTIVOS": int(activos),
                    "APRENDICES_CANCELADOS": int(cancelados),
                    "TOTAL_HORAS": float(horas),
                    "ESTADO_REGISTRO": status,
                }
            )

        for issue in issues:
            audit_rows.append(
                {
                    "ID_REGISTRO_UUID": record_uuid,
                    "ID_PERIODO": period if period is not None else "",
                    "CODIGO_FICHA": ficha if ficha is not None else "",
                    "FILA_ORIGEN": source_row,
                    "REGLA": issue["REGLA"],
                    "SEVERIDAD": issue["SEVERIDAD"],
                    "CAMPO": issue["CAMPO"],
                    "VALOR_CRUDO": issue["VALOR_CRUDO"],
                    "DETALLE": issue["DETALLE"],
                }
            )

    curated = pd.DataFrame(curated_rows, columns=CURATED_COLUMNS)
    audit = pd.DataFrame(audit_rows, columns=AUDIT_COLUMNS)
    rejected_columns = list(frame.columns) + ["FILA_ORIGEN", "MOTIVO_RECHAZO"]
    rejected = pd.DataFrame(rejected_rows, columns=rejected_columns)

    if not curated.empty:
        curated["ID_PERIODO"] = curated["ID_PERIODO"].astype("int64")
        curated["CODIGO_FICHA"] = curated["CODIGO_FICHA"].astype("int64")
        curated["CODIGO_JORNADA"] = curated["CODIGO_JORNADA"].astype("int64")
        for column in (
            "APRENDICES_MATRICULADOS",
            "APRENDICES_ACTIVOS",
            "APRENDICES_CANCELADOS",
        ):
            curated[column] = curated[column].astype("int64")
        curated["TOTAL_HORAS"] = curated["TOTAL_HORAS"].astype("float64")

    # --- Postcondiciones {Q} ---
    postcondition(not curated.empty, "El conjunto curado T_PE04 no puede estar vacío.")
    postcondition(
        len(curated) + len(rejected) == len(frame),
        "Toda fila debe terminar aceptada o rechazada (conservación de cardinalidad).",
    )
    postcondition(
        curated["ID_REGISTRO_UUID"].notna().all() and curated["ID_REGISTRO_UUID"].is_unique,
        "La PK ID_REGISTRO_UUID debe ser NOT NULL y única.",
    )
    postcondition(
        (curated["CODIGO_FICHA"] > 0).all(),
        "CODIGO_FICHA debe ser positivo en todas las filas curadas.",
    )
    postcondition(
        curated["ID_PERIODO"].map(lambda value: parse_period(value) is not None).all(),
        "ID_PERIODO debe cumplir el dominio YYYYMM.",
    )
    postcondition(
        curated["CODIGO_JORNADA"].isin([1, 2, 3, 4]).all(),
        "CODIGO_JORNADA debe pertenecer a {1,2,3,4}.",
    )
    postcondition(
        (curated[["APRENDICES_MATRICULADOS", "APRENDICES_ACTIVOS", "APRENDICES_CANCELADOS"]] >= 0)
        .all()
        .all(),
        "Los conteos de aprendices deben ser >= 0.",
    )
    postcondition(
        (curated["TOTAL_HORAS"] >= 0).all(),
        "TOTAL_HORAS debe ser >= 0.00.",
    )
    postcondition(
        ((curated["TOTAL_HORAS"] * 100).round() / 100 - curated["TOTAL_HORAS"]).abs().max()
        < 1e-9,
        "TOTAL_HORAS debe estar cuantizado a 2 decimales.",
    )
    postcondition(
        curated["ESTADO_REGISTRO"].isin(["OK", "REVISAR"]).all(),
        "ESTADO_REGISTRO debe pertenecer a {OK, REVISAR}.",
    )
    postcondition(
        curated["ID_PERIODO"].nunique() == 1,
        "Un archivo mensual PE-04 debe producir un único periodo; se detectaron varios.",
    )

    meta.rows_accepted = len(curated)
    meta.rows_rejected = len(rejected)
    meta.rows_in_review = int((curated["ESTADO_REGISTRO"] == "REVISAR").sum())
    meta.rows_rounded = rounded_count
    meta.unique_fichas = int(curated["CODIGO_FICHA"].nunique())
    meta.total_hours = round(float(curated["TOTAL_HORAS"].sum()), 2)
    meta.total_matriculados = int(curated["APRENDICES_MATRICULADOS"].sum())
    meta.total_activos = int(curated["APRENDICES_ACTIVOS"].sum())
    meta.source_rows_nonblank = len(frame)
    meta.period = int(curated["ID_PERIODO"].iloc[0])
    if "IDPERIODO" in frame.columns and frame["IDPERIODO"].map(parse_period).notna().any():
        meta.period_source = "columna IDPERIODO"
    elif period_was_derived:
        meta.period_source = period_source_hint or "nombre_archivo"
    else:
        meta.period_source = "no_disponible"
    if meta.cancelados_disponible is False:
        meta.warnings.append(
            "APRENDICES_CANCELADOS no existe en la fuente; se aplica DEFAULT 0.00."
        )
    if meta.activos_disponible is False:
        meta.warnings.append(
            "TOTAL_APRENDICES_ACTIVOS no existe en la fuente; se aplica DEFAULT 0."
        )
    if meta.rows_rejected:
        meta.warnings.append(f"{meta.rows_rejected} fila(s) rechazada(s); ver auditoría/rechazados.")

    return NormalizationResult(curated=curated, audit=audit, rejected=rejected, metadata=meta)


def canonicalize_curated(frame: pd.DataFrame) -> pd.DataFrame:
    """Reaplica las reglas 1FN sobre un conjunto ya curado.

    Es la función que permite verificar f(f(x)) = f(x): sobre una tabla ya
    normalizada no debe cambiar ningún atributo del esquema objetivo.
    """
    precondition(
        all(column in frame.columns for column in CURATED_COLUMNS),
        f"Faltan columnas del esquema 1FN: {CURATED_COLUMNS}",
    )
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        period = parse_period(row.get("ID_PERIODO"))
        ficha, _ = parse_integer(row.get("CODIGO_FICHA"))
        programa = clean_program_name(row.get("NOMBRE_PROGRAMA"))
        jornada = map_jornada(row.get("CODIGO_JORNADA"))
        matriculados, _ = parse_integer(row.get("APRENDICES_MATRICULADOS"))
        activos, _ = parse_integer(row.get("APRENDICES_ACTIVOS"))
        cancelados, _ = parse_integer(row.get("APRENDICES_CANCELADOS"))
        horas, _, _ = quantize_hours(row.get("TOTAL_HORAS"))
        status = clean_text(row.get("ESTADO_REGISTRO")).upper()
        if status not in {"OK", "REVISAR"}:
            status = "REVISAR"
        rows.append(
            {
                "ID_REGISTRO_UUID": (
                    make_record_uuid(period, ficha, programa, jornada)
                    if period is not None
                    and ficha is not None
                    and ficha > 0
                    and programa
                    and jornada is not None
                    else ""
                ),
                "ID_PERIODO": int(period) if period is not None else 0,
                "CODIGO_FICHA": int(ficha) if ficha is not None else 0,
                "NOMBRE_PROGRAMA": programa,
                "CODIGO_JORNADA": int(jornada) if jornada is not None else 4,
                "APRENDICES_MATRICULADOS": int(matriculados or 0),
                "APRENDICES_ACTIVOS": int(activos or 0),
                "APRENDICES_CANCELADOS": int(cancelados or 0),
                "TOTAL_HORAS": float(horas or Decimal("0.00")),
                "ESTADO_REGISTRO": status,
            }
        )
    return pd.DataFrame(rows, columns=CURATED_COLUMNS)


def has_residual_composite(value: Any) -> bool:
    return bool(_RESIDUAL_COMPOSITE_RE.fullmatch(clean_text(value)))
