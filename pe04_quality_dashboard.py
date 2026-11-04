"""CLI del tablero de validación de calidad de datos PE-04.

Genera:
  reports/PE04/YYYYMM/pe04_quality_dashboard.html
  reports/PE04/YYYYMM/pe04_quality_report.json
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from pe04_etl.contracts import ContractViolation
from pe04_etl.persist import write_quality_html, write_quality_json
from pe04_etl.pipeline import run_pipeline
from pe04_etl.quality import build_quality_report

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = PROJECT_ROOT / "PE-04_mayo 2026.xlsx"
DEFAULT_DATA_OUTPUT = PROJECT_ROOT / "data" / "curated" / "PE04"
DEFAULT_REPORTS_OUTPUT = PROJECT_ROOT / "reports" / "PE04"


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:  # pragma: no cover
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ejecuta el ETL PE-04 y genera el tablero de calidad de datos."
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Archivo XLSX/CSV PE-04.")
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT, help="Raíz del dataset curado.")
    parser.add_argument("--reports-output", type=Path, default=DEFAULT_REPORTS_OUTPUT, help="Raíz de reportes.")
    parser.add_argument("--period", default=None, help="Periodo YYYYMM.")
    parser.add_argument("--open", action="store_true", help="Abre el tablero HTML al terminar.")
    parser.add_argument(
        "--fail-on-critical",
        action="store_true",
        help="Devuelve código 1 si hay fallos críticos (útil en CI).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)
    try:
        result = run_pipeline(args.source, args.data_output, period=args.period)
    except ContractViolation as exc:
        print(f"ERROR DE CONTRATO {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - borde operativo
        print(f"ERROR CRÍTICO: {exc}", file=sys.stderr)
        return 1

    report = build_quality_report(
        result.curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    json_path = write_quality_json(report, args.reports_output, result.metadata.period)
    html_path = write_quality_html(report, args.reports_output, result.metadata.period)

    print("TABLERO DE CALIDAD PE-04 · ejecución exitosa")
    print(f"  Estado global......: {report.global_status}")
    print(f"  Reglas aprobadas...: {report.passed_rules}/{len(report.applicable)}")
    print(f"  Fallos críticos....: {report.critical_failures}")
    print(f"  Filas conformes....: {report.row_conformity:.1f}%")
    print(f"  Registros curados..: {len(result.curated)}")
    print(f"  Tablero HTML.......: {html_path}")
    print(f"  Reporte JSON.......: {json_path}")
    if report.critical_failures:
        print("  Revisar hallazgos críticos en el tablero.", file=sys.stderr)

    if args.open:
        webbrowser.open(Path(html_path).resolve().as_uri())
    if args.fail_on_critical and report.critical_failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
