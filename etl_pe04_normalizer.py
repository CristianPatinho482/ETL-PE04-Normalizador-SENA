"""CLI del módulo ETL-PE04-NORMALIZER.

Uso:
    python etl_pe04_normalizer.py
    python etl_pe04_normalizer.py --source "PE04"
    python etl_pe04_normalizer.py --source "PE-04_JUN 2026.csv" --output data/curated/PE04
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pe04_etl.contracts import ContractViolation
from pe04_etl.pipeline import BatchResult, PipelineResult, run_batch, run_pipeline

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "curated" / "PE04"


def _default_source() -> Path:
    folder = PROJECT_ROOT / "PE04"
    if folder.exists():
        return folder
    return PROJECT_ROOT / "PE-04_mayo 2026.xlsx"


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:  # pragma: no cover
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Normaliza el reporte PE-04 del SENA ASTIN a Primera Forma Normal (1FN)."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=_default_source(),
        help="Archivo o carpeta PE-04 (XLSX, XLSM, CSV o XML SpreadsheetML).",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directorio raíz de salida.")
    parser.add_argument("--period", default=None, help="Periodo YYYYMM (solo para un archivo).")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="En modo carpeta, procesa solo el primer nivel y no subcarpetas.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="En modo carpeta, detiene el lote en el primer archivo con error.",
    )
    return parser


def _print_single(result: PipelineResult) -> None:
    meta = result.metadata
    print("ETL-PE04-NORMALIZER · ejecución exitosa")
    print(f"  Fuente............: {meta.source_name}")
    print(f"  Formato...........: {meta.source_kind}")
    print(f"  Cabecera (1-based) : {meta.header_row + 1}")
    print(f"  Periodo............: {meta.period} ({meta.period_source})")
    print(f"  Filas aceptadas....: {meta.rows_accepted}")
    print(f"  Filas rechazadas...: {meta.rows_rejected}")
    print(f"  Filas en REVISAR...: {meta.rows_in_review}")
    print(f"  Horas redondeadas..: {meta.rows_rounded}")
    print(f"  CSV curado.........: {result.paths.get('curated', '—')}")
    print(f"  SHA-256............: {meta.curated_sha256 or '—'}")
    if meta.warnings:
        print("  Advertencias:")
        for warning in meta.warnings:
            print(f"    - {warning}")


def _print_batch(batch: BatchResult) -> None:
    print("ETL-PE04-NORMALIZER · ejecución por lote")
    print(f"  Carpeta............: {batch.source}")
    print(f"  Archivos detectados: {batch.files_total}")
    for item in batch.items:
        if item.status == "OK":
            print(
                f"  [OK] {item.source.name} · periodo {item.period} · "
                f"{item.rows_accepted} aceptadas · {item.rows_rejected} rechazadas"
            )
        elif item.status == "CONFLICT":
            print(f"  [CONFLICTO] {item.source.name}: {item.error}")
        else:
            print(f"  [ERROR] {item.source.name}: {item.error}")
    if not batch.items:
        print("  Sin archivos procesados.")
    periods = ", ".join(str(period) for period in batch.periods) or "—"
    print(f"  Periodos generados.: {periods}")
    print(f"  Archivos con error..: {len(batch.error_items)}")
    print(f"  Conflictos..........: {len(batch.conflict_items)}")
    if len(batch.items) < batch.files_total:
        print(f"  No procesados.......: {batch.files_total - len(batch.items)} (fail-fast)")
    print(f"  Manifiesto.........: {batch.manifest_path or '—'}")


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)

    if args.source.is_dir() and args.period is not None:
        print(
            "ERROR: --period solo aplica cuando --source es un archivo. "
            "En modo carpeta el periodo se infiere de cada archivo.",
            file=sys.stderr,
        )
        return 2

    try:
        if args.source.is_dir():
            batch = run_batch(
                args.source,
                args.output,
                recursive=not args.no_recursive,
                fail_fast=args.fail_fast,
            )
            _print_batch(batch)
            return 1 if batch.has_errors else 0
        result = run_pipeline(args.source, args.output, period=args.period)
    except ContractViolation as exc:
        print(f"ERROR DE CONTRATO {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - borde operativo
        print(f"ERROR CRÍTICO: {exc}", file=sys.stderr)
        return 1

    _print_single(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
