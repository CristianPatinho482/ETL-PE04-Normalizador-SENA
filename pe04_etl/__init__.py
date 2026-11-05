"""ETL-PE04-NORMALIZER: normalización 1FN del reporte PE-04 (SENA ASTIN)."""

from .contracts import MODULE_ID, MODULE_VERSION, ContractViolation, ProcessingMetadata
from .io import discover_sources, extract_pe04, read_raw_table
from .normalize import (
    CURATED_COLUMNS,
    UUID_NAMESPACE,
    canonicalize_curated,
    normalize_dataframe,
    parse_composite,
    parse_decimal,
    parse_hours,
    parse_integer,
    parse_period,
    period_from_filename,
    period_from_preamble,
)
from .pipeline import (
    BatchItem,
    BatchResult,
    PipelineResult,
    run_batch,
    run_pipeline,
)

__all__ = [
    "CURATED_COLUMNS",
    "MODULE_ID",
    "MODULE_VERSION",
    "UUID_NAMESPACE",
    "BatchItem",
    "BatchResult",
    "ContractViolation",
    "PipelineResult",
    "ProcessingMetadata",
    "canonicalize_curated",
    "discover_sources",
    "extract_pe04",
    "normalize_dataframe",
    "parse_composite",
    "parse_decimal",
    "parse_hours",
    "parse_integer",
    "parse_period",
    "period_from_filename",
    "period_from_preamble",
    "read_raw_table",
    "run_batch",
    "run_pipeline",
]
