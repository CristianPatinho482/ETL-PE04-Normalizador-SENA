"""ETL-PE04-NORMALIZER: normalización 1FN del reporte PE-04 (SENA ASTIN)."""

from .contracts import MODULE_ID, MODULE_VERSION, ContractViolation, ProcessingMetadata
from .normalize import (
    CURATED_COLUMNS,
    UUID_NAMESPACE,
    canonicalize_curated,
    normalize_dataframe,
    parse_composite,
    parse_decimal,
    parse_integer,
    parse_period,
    period_from_filename,
)
from .pipeline import PipelineResult, run_pipeline

__all__ = [
    "CURATED_COLUMNS",
    "MODULE_ID",
    "MODULE_VERSION",
    "UUID_NAMESPACE",
    "ContractViolation",
    "PipelineResult",
    "ProcessingMetadata",
    "canonicalize_curated",
    "normalize_dataframe",
    "parse_composite",
    "parse_decimal",
    "parse_integer",
    "parse_period",
    "period_from_filename",
    "run_pipeline",
]
