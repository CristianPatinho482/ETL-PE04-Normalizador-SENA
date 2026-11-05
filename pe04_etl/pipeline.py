"""Orquestación del pipeline ETL PE-04."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import (
    MODULE_ID,
    MODULE_VERSION,
    ContractViolation,
    ProcessingMetadata,
    precondition,
)
from .io import (
    ExtractResult,
    RawTable,
    discover_sources,
    extract_pe04,
    read_raw_table,
)
from .normalize import (
    UUID_NAMESPACE,
    NormalizationResult,
    normalize_dataframe,
    parse_period,
    period_from_filename,
    period_from_preamble,
)
from .persist import (
    METADATA_FILENAME,
    write_audit_csv,
    write_batch_manifest,
    write_curated_csv,
    write_metadata_json,
    write_rejected_csv,
)


@dataclass
class PipelineResult:
    source: Path
    raw: RawTable
    extract: ExtractResult
    normalization: NormalizationResult
    metadata: ProcessingMetadata
    paths: dict[str, Any]

    @property
    def curated(self):
        return self.normalization.curated

    @property
    def audit(self):
        return self.normalization.audit

    @property
    def rejected(self):
        return self.normalization.rejected

    def persist(self, output_dir: str | Path) -> dict[str, Any]:
        """Persiste el resultado y devuelve las rutas generadas."""
        return _persist_result(self, output_dir)


def _persist_result(result: PipelineResult, output_dir: str | Path) -> dict[str, Any]:
    """Escribe los artefactos de un resultado ya normalizado.

    Se separa de ``run_pipeline`` para que el modo lote procese todos los
    archivos antes de escribir y pueda detectar periodos duplicados sin dejar
    salidas parciales.
    """
    meta = result.metadata
    precondition(meta.period is not None, "No se puede persistir sin un periodo de proceso.")
    output_root = Path(output_dir)
    curated_info = write_curated_csv(result.normalization.curated, output_root, meta.period)
    meta.output_dir = str((output_root / str(meta.period)).resolve())
    meta.curated_path = curated_info["path"]
    meta.curated_sha256 = curated_info["sha256"]
    meta.curated_bytes = curated_info["bytes"]
    meta.output_bom = curated_info["bom"]
    meta.audit_path = write_audit_csv(result.normalization.audit, output_root, meta.period)
    meta.rejected_path = write_rejected_csv(result.normalization.rejected, output_root, meta.period)
    meta.metadata_path = str((output_root / str(meta.period) / METADATA_FILENAME).resolve())
    write_metadata_json(meta, output_root)
    result.paths.update(
        {
            "curated": meta.curated_path,
            "audit": meta.audit_path,
            "rejected": meta.rejected_path,
            "metadata": meta.metadata_path,
            "curated_sha256": meta.curated_sha256,
            "sidecar": curated_info["sidecar"],
        }
    )
    return result.paths


def run_pipeline(
    source_path: str | Path,
    output_dir: str | Path | None = None,
    period: int | str | None = None,
    *,
    write_outputs: bool = True,
) -> PipelineResult:
    """Lee, normaliza y (opcionalmente) persiste un archivo PE-04.

    {P}: la fuente existe, no es un archivo fantasma y contiene una cabecera
    PE-04 reconocible. {Q}: el conjunto curado cumple el esquema 1FN y toda
    fila quedó aceptada o rechazada con trazabilidad.
    """
    source = Path(source_path)
    raw = read_raw_table(source)
    extracted = extract_pe04(raw)

    explicit_period = parse_period(period) if period is not None else None
    if period is not None:
        precondition(explicit_period is not None, f"Periodo explícito inválido: {period!r}")

    preamble_period = period_from_preamble(raw.frame, max_rows=extracted.header_row)
    name_period = period_from_filename(source.name)
    period_hint = explicit_period or preamble_period or name_period
    if explicit_period is not None:
        period_source_hint = "argumento_cli"
    elif preamble_period is not None:
        period_source_hint = "preambulo"
    else:
        period_source_hint = "nombre_archivo"

    metadata = ProcessingMetadata(
        source_path=str(source.resolve()),
        source_name=source.name,
        source_kind=raw.kind,
        source_sha256=raw.sha256,
        source_bytes=raw.size_bytes,
        encoding=raw.encoding,
        delimiter=raw.delimiter,
        sheet_name=raw.sheet_name,
        header_row=extracted.header_row,
        source_rows_total=len(raw.frame),
        blank_rows_skipped=extracted.blank_rows_skipped,
        columns_detected=extracted.columns,
        warnings=list(extracted.warnings),
        uuid_namespace=str(UUID_NAMESPACE),
    )
    metadata.columns_missing_optional = [
        column
        for column in ("TOTAL_APRENDICES_ACTIVOS", "TOTAL_APRENDICES_CANCELADOS")
        if column not in extracted.columns
    ]
    if preamble_period is not None and name_period is not None and preamble_period != name_period:
        metadata.warnings.append(
            f"El periodo del preámbulo ({preamble_period}) difiere del nombre del archivo "
            f"({name_period}); prevalece el preámbulo."
        )
    if explicit_period is not None and preamble_period is not None and explicit_period != preamble_period:
        metadata.warnings.append(
            f"El periodo explícito ({explicit_period}) difiere del preámbulo "
            f"({preamble_period}); prevalece el argumento CLI."
        )

    normalization = normalize_dataframe(
        extracted.frame,
        metadata=metadata,
        period_hint=period_hint,
        period_source_hint=period_source_hint,
        first_data_row=extracted.header_row + 2,
    )
    metadata.source_rows_nonblank = extracted.source_rows_nonblank
    metadata.blank_rows_skipped = extracted.blank_rows_skipped

    result = PipelineResult(
        source=source,
        raw=raw,
        extract=extracted,
        normalization=normalization,
        metadata=metadata,
        paths={},
    )
    if output_dir is not None and write_outputs:
        _persist_result(result, output_dir)
    return result


@dataclass
class BatchItem:
    source: Path
    status: str = "PENDING"
    result: PipelineResult | None = None
    period: int | None = None
    error: str | None = None

    @property
    def rows_accepted(self) -> int:
        return self.result.metadata.rows_accepted if self.result is not None else 0

    @property
    def rows_rejected(self) -> int:
        return self.result.metadata.rows_rejected if self.result is not None else 0

    @property
    def rows_in_review(self) -> int:
        return self.result.metadata.rows_in_review if self.result is not None else 0

    def to_dict(self) -> dict[str, Any]:
        metadata = self.result.metadata.to_dict() if self.result is not None else None
        return {
            "source": self.source.name,
            "path": str(self.source.resolve()),
            "status": self.status,
            "period": self.period,
            "rows_accepted": self.rows_accepted,
            "rows_rejected": self.rows_rejected,
            "rows_in_review": self.rows_in_review,
            "error": self.error,
            "paths": dict(self.result.paths) if self.result is not None else {},
            "metadata": metadata,
        }


@dataclass
class BatchResult:
    source: Path
    files_total: int
    recursive: bool = True
    items: list[BatchItem] = field(default_factory=list)
    manifest_path: str | None = None
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    @property
    def ok_items(self) -> list[BatchItem]:
        return [item for item in self.items if item.status == "OK"]

    @property
    def error_items(self) -> list[BatchItem]:
        return [item for item in self.items if item.status == "ERROR"]

    @property
    def conflict_items(self) -> list[BatchItem]:
        return [item for item in self.items if item.status == "CONFLICT"]

    @property
    def periods(self) -> list[int]:
        return sorted(
            {
                item.period
                for item in self.ok_items
                if item.period is not None
            }
        )

    @property
    def has_errors(self) -> bool:
        return bool(self.error_items or self.conflict_items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": MODULE_ID,
            "module_version": MODULE_VERSION,
            "generated_at": self.generated_at,
            "source": str(self.source.resolve()),
            "recursive": self.recursive,
            "files_total": self.files_total,
            "files_processed": len(self.items),
            "files_ok": len(self.ok_items),
            "files_error": len(self.error_items),
            "files_conflict": len(self.conflict_items),
            "files_not_processed": max(self.files_total - len(self.items), 0),
            "periods": self.periods,
            "items": [item.to_dict() for item in self.items],
        }


def run_batch(
    source_path: str | Path,
    output_dir: str | Path | None = None,
    *,
    recursive: bool = True,
    write_outputs: bool = True,
    fail_fast: bool = False,
) -> BatchResult:
    """Normaliza todos los archivos PE-04 de una carpeta.

    Cada periodo se escribe en su propia partición ``YYYYMM``. Si dos archivos
    declaran el mismo periodo, ambos se marcan como conflicto y no se escribe
    ninguno para evitar sobrescrituras silenciosas. Un error en un archivo no
    impide procesar los demás, salvo que ``fail_fast`` sea verdadero.
    """
    source = Path(source_path)
    files = discover_sources(
        source,
        recursive=recursive,
        exclude_dirs=(output_dir,) if output_dir is not None else (),
    )
    batch = BatchResult(source=source, files_total=len(files), recursive=recursive)

    for file in files:
        try:
            result = run_pipeline(file, output_dir=None, write_outputs=False)
        except ContractViolation as exc:
            batch.items.append(
                BatchItem(source=file, status="ERROR", error=str(exc))
            )
        except Exception as exc:  # noqa: BLE001 - se reporta por archivo
            batch.items.append(
                BatchItem(
                    source=file,
                    status="ERROR",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            batch.items.append(
                BatchItem(
                    source=file,
                    status="OK",
                    result=result,
                    period=result.metadata.period,
                )
            )
        if fail_fast and batch.items[-1].status == "ERROR":
            break

    by_period: dict[int, list[BatchItem]] = {}
    for item in batch.items:
        if item.status == "OK" and item.period is not None:
            by_period.setdefault(item.period, []).append(item)

    for period, group in by_period.items():
        if len(group) > 1:
            names = ", ".join(item.source.name for item in group)
            for item in group:
                item.status = "CONFLICT"
                item.error = (
                    f"Periodo {period} duplicado en: {names}. "
                    "No se escribió para evitar sobrescritura."
                )

    if output_dir is not None and write_outputs:
        for period, group in by_period.items():
            if len(group) != 1:
                continue
            item = group[0]
            assert item.result is not None
            _persist_result(item.result, output_dir)
        batch.manifest_path = write_batch_manifest(batch.to_dict(), output_dir)

    return batch
