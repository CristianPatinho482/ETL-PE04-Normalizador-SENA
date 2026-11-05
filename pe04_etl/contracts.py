"""Contratos formales (Design-by-Contract) del módulo ETL PE-04.

Cada cláusula se nombra con la notación de Hoare:
    P = precondición, Q = postcondición, I = invariante.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MODULE_VERSION = "1.2.0"
MODULE_ID = "ETL-PE04-NORMALIZER"


class ContractViolation(AssertionError):
    """Violación de una cláusula {P}, {Q} o {I}."""

    def __init__(self, clause: str, message: str):
        self.clause = clause
        self.message = message
        super().__init__(f"[{clause}] {message}")


def precondition(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation("P", message)


def postcondition(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation("Q", message)


def invariant(condition: bool, message: str) -> None:
    if not condition:
        raise ContractViolation("I", message)


@dataclass
class ProcessingMetadata:
    """Trazabilidad completa de una ejecución del pipeline."""

    module_id: str = MODULE_ID
    module_version: str = MODULE_VERSION
    source_path: str = ""
    source_name: str = ""
    source_kind: str = ""
    source_sha256: str = ""
    source_bytes: int = 0
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    header_row: int = -1
    source_rows_total: int = 0
    source_rows_nonblank: int = 0
    blank_rows_skipped: int = 0
    rows_accepted: int = 0
    rows_rejected: int = 0
    rows_in_review: int = 0
    rows_rounded: int = 0
    unique_fichas: int = 0
    total_hours: float = 0.0
    total_matriculados: int = 0
    total_activos: int = 0
    columns_detected: list[str] = field(default_factory=list)
    columns_missing_optional: list[str] = field(default_factory=list)
    required_columns_present: bool = False
    period: int | None = None
    period_source: str = ""
    cancelados_disponible: bool = False
    activos_disponible: bool = False
    uuid_strategy: str = "uuid5-determinista"
    uuid_namespace: str = ""
    warnings: list[str] = field(default_factory=list)
    output_dir: str = ""
    curated_path: str = ""
    audit_path: str = ""
    rejected_path: str = ""
    metadata_path: str = ""
    curated_sha256: str = ""
    curated_bytes: int = 0
    output_bom: bool = False
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_version": self.module_version,
            "source_path": self.source_path,
            "source_name": self.source_name,
            "source_kind": self.source_kind,
            "source_sha256": self.source_sha256,
            "source_bytes": self.source_bytes,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "source_rows_total": self.source_rows_total,
            "source_rows_nonblank": self.source_rows_nonblank,
            "blank_rows_skipped": self.blank_rows_skipped,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "rows_in_review": self.rows_in_review,
            "rows_rounded": self.rows_rounded,
            "unique_fichas": self.unique_fichas,
            "total_hours": self.total_hours,
            "total_matriculados": self.total_matriculados,
            "total_activos": self.total_activos,
            "columns_detected": list(self.columns_detected),
            "columns_missing_optional": list(self.columns_missing_optional),
            "required_columns_present": self.required_columns_present,
            "period": self.period,
            "period_source": self.period_source,
            "cancelados_disponible": self.cancelados_disponible,
            "activos_disponible": self.activos_disponible,
            "uuid_strategy": self.uuid_strategy,
            "uuid_namespace": self.uuid_namespace,
            "warnings": list(self.warnings),
            "output_dir": self.output_dir,
            "curated_path": self.curated_path,
            "audit_path": self.audit_path,
            "rejected_path": self.rejected_path,
            "metadata_path": self.metadata_path,
            "curated_sha256": self.curated_sha256,
            "curated_bytes": self.curated_bytes,
            "output_bom": self.output_bom,
            "generated_at": self.generated_at,
        }
