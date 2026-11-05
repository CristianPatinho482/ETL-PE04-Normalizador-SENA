"""Persistencia atómica y artefactos del pipeline PE-04."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .contracts import ContractViolation, ProcessingMetadata, precondition

CURATED_FILENAME = "pe04_curated.csv"
AUDIT_FILENAME = "pe04_auditoria.csv"
REJECTED_FILENAME = "pe04_rechazados.csv"
METADATA_FILENAME = "pe04_metadata.json"
QUALITY_JSON_FILENAME = "pe04_quality_report.json"
QUALITY_HTML_FILENAME = "pe04_quality_dashboard.html"
BATCH_MANIFEST_FILENAME = "pe04_batch_manifest.json"
QUALITY_BATCH_FILENAME = "pe04_quality_batch.json"


def _replace_with_retry(
    source: Path,
    destination: Path,
    *,
    attempts: int = 8,
    base_delay: float = 0.05,
) -> None:
    """Reemplazo atómico tolerante a bloqueos de Excel/Power BI en Windows.

    WinError 5/32 ocurre cuando otro proceso mantiene abierto el CSV de destino
    sin FILE_SHARE_DELETE. Se reintenta con backoff exponencial; si persiste,
    se informa una causa accionable sin destruir el archivo anterior.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            locked = getattr(exc, "winerror", None) in {5, 32} or exc.errno in {11, 13}
            if not locked or attempt == attempts - 1:
                if locked:
                    raise ContractViolation(
                        "Q",
                        f"No se pudo reemplazar '{destination}' porque otro proceso lo "
                        "mantiene abierto. Cierre el archivo en Excel/Power BI y reintente.",
                    ) from exc
                raise
            time.sleep(base_delay * (2**attempt))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Escribe en un temporal del mismo directorio y renombra con os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary_path, path)
    except Exception:  # noqa: BLE001
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


def _atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    _atomic_write_bytes(path, text.encode(encoding))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv_bytes(frame: pd.DataFrame, *, float_format: str | None = None) -> bytes:
    buffer = io.StringIO()
    frame.to_csv(
        buffer,
        index=False,
        sep=";",
        lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
        float_format=float_format,
    )
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _write_sidecar(target: Path, digest: str) -> Path:
    sidecar = target.with_name(target.name + ".sha256")
    _atomic_write_text(sidecar, f"{digest}  {target.name}\n")
    return sidecar


def write_curated_csv(
    frame: pd.DataFrame,
    base_output_dir: str | Path,
    period: int | None,
) -> dict[str, Any]:
    precondition(period is not None, "No se puede persistir sin un periodo de proceso.")
    period_text = str(int(period))
    precondition(
        len(period_text) == 6 and period_text.isdigit(),
        f"Periodo de partición inválido: {period!r}",
    )
    target = Path(base_output_dir) / period_text / CURATED_FILENAME
    data = _csv_bytes(frame, float_format="%.2f")
    _atomic_write_bytes(target, data)
    digest = sha256_bytes(data)
    sidecar = _write_sidecar(target, digest)
    return {
        "path": str(target),
        "sha256": digest,
        "bytes": len(data),
        "bom": data.startswith(b"\xef\xbb\xbf"),
        "sidecar": str(sidecar),
    }


def write_audit_csv(
    frame: pd.DataFrame,
    base_output_dir: str | Path,
    period: int | None,
) -> str:
    precondition(period is not None, "No se puede persistir la auditoría sin periodo.")
    target = Path(base_output_dir) / str(int(period)) / AUDIT_FILENAME
    _atomic_write_bytes(target, _csv_bytes(frame))
    return str(target)


def write_rejected_csv(
    frame: pd.DataFrame,
    base_output_dir: str | Path,
    period: int | None,
) -> str:
    precondition(period is not None, "No se puede persistir los rechazados sin periodo.")
    target = Path(base_output_dir) / str(int(period)) / REJECTED_FILENAME
    _atomic_write_bytes(target, _csv_bytes(frame))
    return str(target)


def write_metadata_json(metadata: ProcessingMetadata, base_output_dir: str | Path) -> str:
    precondition(metadata.period is not None, "No se puede persistir metadata sin periodo.")
    target = Path(base_output_dir) / str(int(metadata.period)) / METADATA_FILENAME
    payload = json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2)
    _atomic_write_text(target, payload + "\n")
    return str(target)


def write_batch_manifest(payload: dict[str, Any], output_dir: str | Path) -> str:
    """Escribe el manifiesto de una ejecución por lote."""
    target = Path(output_dir) / BATCH_MANIFEST_FILENAME
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    _atomic_write_text(target, text + "\n")
    return str(target)


def write_quality_batch_json(payload: dict[str, Any], output_dir: str | Path) -> str:
    """Escribe el resumen de calidad de todos los periodos de un lote."""
    target = Path(output_dir) / QUALITY_BATCH_FILENAME
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    _atomic_write_text(target, text + "\n")
    return str(target)


def write_quality_json(report: Any, output_dir: str | Path, period: int | None) -> str:
    precondition(period is not None, "No se puede persistir el reporte de calidad sin periodo.")
    target = Path(output_dir) / str(int(period)) / QUALITY_JSON_FILENAME
    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str)
    _atomic_write_text(target, payload + "\n")
    return str(target)


def write_quality_html(report: Any, output_dir: str | Path, period: int | None) -> str:
    precondition(period is not None, "No se puede persistir el tablero sin periodo.")
    target = Path(output_dir) / str(int(period)) / QUALITY_HTML_FILENAME
    _atomic_write_text(target, report.render_html(), encoding="utf-8")
    return str(target)
