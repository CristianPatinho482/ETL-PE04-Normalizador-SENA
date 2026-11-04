"""Orquestación del pipeline ETL PE-04."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import ProcessingMetadata, precondition
from .io import ExtractResult, RawTable, extract_pe04, read_raw_table
from .normalize import (
    UUID_NAMESPACE,
    NormalizationResult,
    normalize_dataframe,
    parse_period,
    period_from_filename,
)
from .persist import (
    METADATA_FILENAME,
    write_audit_csv,
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

    name_period = period_from_filename(source.name)
    period_hint = explicit_period or name_period
    period_source_hint = "argumento_cli" if explicit_period is not None else "nombre_archivo"

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

    normalization = normalize_dataframe(
        extracted.frame,
        metadata=metadata,
        period_hint=period_hint,
        period_source_hint=period_source_hint,
        first_data_row=extracted.header_row + 2,
    )
    metadata.source_rows_nonblank = extracted.source_rows_nonblank
    metadata.blank_rows_skipped = extracted.blank_rows_skipped

    paths: dict[str, Any] = {}
    if output_dir is not None and write_outputs:
        output_root = Path(output_dir)
        curated_info = write_curated_csv(
            normalization.curated,
            output_root,
            metadata.period,
        )
        metadata.output_dir = str((output_root / str(metadata.period)).resolve())
        metadata.curated_path = curated_info["path"]
        metadata.curated_sha256 = curated_info["sha256"]
        metadata.curated_bytes = curated_info["bytes"]
        metadata.output_bom = curated_info["bom"]
        metadata.audit_path = write_audit_csv(normalization.audit, output_root, metadata.period)
        metadata.rejected_path = write_rejected_csv(normalization.rejected, output_root, metadata.period)
        metadata.metadata_path = str(
            (Path(output_root) / str(metadata.period) / METADATA_FILENAME).resolve()
        )
        write_metadata_json(metadata, output_root)
        paths.update(
            {
                "curated": metadata.curated_path,
                "audit": metadata.audit_path,
                "rejected": metadata.rejected_path,
                "metadata": metadata.metadata_path,
                "curated_sha256": metadata.curated_sha256,
                "sidecar": curated_info["sidecar"],
            }
        )

    return PipelineResult(
        source=source,
        raw=raw,
        extract=extracted,
        normalization=normalization,
        metadata=metadata,
        paths=paths,
    )
