"""CLI del tablero de validación de calidad de datos PE-04.

Genera, para cada periodo:
  reports/PE04/YYYYMM/pe04_quality_dashboard.html
  reports/PE04/YYYYMM/pe04_quality_report.json

En modo carpeta genera además:
  reports/PE04/pe04_quality_batch.json
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path
from typing import Any

from pe04_etl.contracts import ContractViolation
from pe04_etl.persist import (
    write_quality_batch_json,
    write_quality_html,
    write_quality_json,
)
from pe04_etl.pipeline import PipelineResult, run_batch, run_pipeline
from pe04_etl.quality import QualityReport, build_quality_report

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_OUTPUT = PROJECT_ROOT / "data" / "curated" / "PE04"
DEFAULT_REPORTS_OUTPUT = PROJECT_ROOT / "reports" / "PE04"


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
        description="Ejecuta el ETL PE-04 y genera el tablero de calidad de datos."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=_default_source(),
        help="Archivo o carpeta PE-04 (XLSX, XLSM, CSV o XML SpreadsheetML).",
    )
    parser.add_argument("--data-output", type=Path, default=DEFAULT_DATA_OUTPUT, help="Raíz del dataset curado.")
    parser.add_argument("--reports-output", type=Path, default=DEFAULT_REPORTS_OUTPUT, help="Raíz de reportes.")
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
    parser.add_argument("--open", action="store_true", help="Abre el primer tablero HTML al terminar.")
    parser.add_argument(
        "--fail-on-critical",
        action="store_true",
        help="Devuelve código 1 si hay fallos críticos (útil en CI).",
    )
    return parser


def _build_report(result: PipelineResult) -> QualityReport:
    return build_quality_report(
        result.curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )


def _write_report(
    report: QualityReport,
    period: int | None,
    reports_output: Path,
) -> tuple[str, str]:
    json_path = write_quality_json(report, reports_output, period)
    html_path = write_quality_html(report, reports_output, period)
    return json_path, html_path


def _run_single(args: argparse.Namespace) -> int:
    result = run_pipeline(args.source, args.data_output, period=args.period)
    report = _build_report(result)
    json_path, html_path = _write_report(report, result.metadata.period, args.reports_output)

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


def _run_batch(args: argparse.Namespace) -> int:
    batch = run_batch(
        args.source,
        args.data_output,
        recursive=not args.no_recursive,
        fail_fast=args.fail_fast,
    )

    summaries: list[dict[str, Any]] = []
    first_html: str | None = None
    critical_failures = 0
    print("TABLERO DE CALIDAD PE-04 · ejecución por lote")
    for item in batch.items:
        if item.status != "OK" or item.result is None:
            summaries.append(
                {
                    "source": item.source.name,
                    "status": item.status,
                    "period": item.period,
                    "error": item.error,
                }
            )
            if item.status == "CONFLICT":
                print(f"  [CONFLICTO] {item.source.name}: {item.error}")
            else:
                print(f"  [ERROR] {item.source.name}: {item.error}")
            continue

        report = _build_report(item.result)
        assert item.period is not None
        json_path, html_path = _write_report(report, item.period, args.reports_output)
        first_html = first_html or html_path
        critical_failures += report.critical_failures
        summary = report.to_dict()["summary"]
        summaries.append(
            {
                "source": item.source.name,
                "status": "OK",
                "period": item.period,
                "rows_accepted": item.rows_accepted,
                "rows_rejected": item.rows_rejected,
                "rows_in_review": item.rows_in_review,
                "global_status": report.global_status,
                "score": summary["score"],
                "row_conformity": summary["row_conformity"],
                "critical_failures": report.critical_failures,
                "json": json_path,
                "html": html_path,
            }
        )
        print(
            f"  [OK] {item.source.name} · periodo {item.period} · "
            f"{report.global_status} · {report.passed_rules}/{len(report.applicable)} reglas"
        )

    payload = {
        "generated_at": batch.generated_at,
        "source": str(batch.source.resolve()),
        "files_total": batch.files_total,
        "files_ok": len(batch.ok_items),
        "files_error": len(batch.error_items),
        "files_conflict": len(batch.conflict_items),
        "periods": batch.periods,
        "items": summaries,
    }
    batch_json = write_quality_batch_json(payload, args.reports_output)

    print(f"  Periodos generados.: {', '.join(str(p) for p in batch.periods) or '—'}")
    print(f"  Fallos críticos....: {critical_failures}")
    print(f"  Resumen del lote...: {batch_json}")
    if len(batch.items) < batch.files_total:
        print(f"  No procesados......: {batch.files_total - len(batch.items)} (fail-fast)")

    if args.open and first_html is not None:
        webbrowser.open(Path(first_html).resolve().as_uri())
    if batch.has_errors:
        return 1
    if args.fail_on_critical and critical_failures:
        return 1
    return 0


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
            return _run_batch(args)
        return _run_single(args)
    except ContractViolation as exc:
        print(f"ERROR DE CONTRATO {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - borde operativo
        print(f"ERROR CRÍTICO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
