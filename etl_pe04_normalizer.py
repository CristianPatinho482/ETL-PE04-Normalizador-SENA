"""CLI del módulo ETL-PE04-NORMALIZER.

Uso:
    python etl_pe04_normalizer.py
    python etl_pe04_normalizer.py --source "PE-04_mayo 2026.xlsx" --output data/curated/PE04
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pe04_etl.contracts import ContractViolation
from pe04_etl.pipeline import run_pipeline

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROJECT_ROOT / "PE-04_mayo 2026.xlsx"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "curated" / "PE04"


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
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Archivo XLSX/CSV de entrada.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Directorio raíz de salida.")
    parser.add_argument("--period", default=None, help="Periodo YYYYMM (si no se infiere de la fuente).")
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)
    try:
        result = run_pipeline(args.source, args.output, period=args.period)
    except ContractViolation as exc:
        print(f"ERROR DE CONTRATO {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - borde operativo
        print(f"ERROR CRÍTICO: {exc}", file=sys.stderr)
        return 1

    meta = result.metadata
    print("ETL-PE04-NORMALIZER · ejecución exitosa")
    print(f"  Fuente............: {meta.source_name}")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
