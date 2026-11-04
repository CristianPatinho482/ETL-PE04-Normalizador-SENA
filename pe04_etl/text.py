"""Utilidades de texto y detección de valores ausentes.

Centraliza la limpieza de caracteres invisibles para que la misma semántica
se use en la lectura, la normalización y las reglas de calidad.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

_INVISIBLES = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
_ESPACIOS = re.compile(r"\s+")


def is_missing(value: Any) -> bool:
    """True para None, NaN, NaT, pd.NA y cadenas vacías/solo espacios."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    try:
        missing = pd.isna(value)
    except (TypeError, ValueError):
        return False
    if isinstance(missing, (bool, np.bool_)):
        return bool(missing)
    return False


def clean_text(value: Any) -> str:
    """Elimina BOM, NBSP, espacios de ancho cero y colapsa whitespace.

    No altera mayúsculas/minúsculas ni acentos: esa decisión se toma en la
    capa de normalización semántica.
    """
    if is_missing(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    text = str(value)
    text = text.replace("\ufeff", "").replace("\u00a0", " ")
    text = text.replace("\u2007", " ").replace("\u202f", " ")
    text = _INVISIBLES.sub("", text)
    text = _ESPACIOS.sub(" ", text)
    return text.strip()


def strip_accents(value: Any) -> str:
    """Remueve diacríticos para comparaciones (no para almacenamiento)."""
    text = clean_text(value)
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_for_match(value: Any) -> str:
    """Canonicaliza un texto para compararlo contra catálogos."""
    return re.sub(r"\s+", " ", strip_accents(value).upper()).strip()


def is_blank_row(values: Any) -> bool:
    """True si todos los valores de una fila son ausentes o cadenas vacías."""
    return all(is_missing(v) or clean_text(v) == "" for v in values)
