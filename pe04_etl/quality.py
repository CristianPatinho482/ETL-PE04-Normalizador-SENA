"""Motor de reglas de calidad y tablero HTML autocontenido para PE-04."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .io import sha256_file
from .normalize import (
    has_residual_composite,
    make_record_uuid,
    parse_decimal,
    parse_integer,
    parse_period,
    period_from_filename,
    quantize_hours,
)
from .text import clean_text, is_missing

SEVERITIES = {"Crítica", "Alta", "Media", "Baja"}


@dataclass
class RuleOutcome:
    rule_id: str
    dimension: str
    severity: str
    description: str
    total: int
    failed: int
    status: str
    details: list[str] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return max(self.total - self.failed, 0)

    @property
    def pct(self) -> float | None:
        if self.total <= 0:
            return None
        return round(100.0 * self.passed / self.total, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "dimension": self.dimension,
            "severity": self.severity,
            "description": self.description,
            "total": int(self.total),
            "failed": int(self.failed),
            "passed": int(self.passed),
            "pct": self.pct,
            "status": self.status,
            "details": list(self.details),
        }


@dataclass
class QualityReport:
    outcomes: list[RuleOutcome]
    metadata: dict[str, Any]
    generated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )

    @property
    def applicable(self) -> list[RuleOutcome]:
        return [outcome for outcome in self.outcomes if outcome.status != "NO_EVALUABLE"]

    @property
    def passed_rules(self) -> int:
        return sum(1 for outcome in self.applicable if outcome.status == "PASS")

    @property
    def failed_rules(self) -> int:
        return sum(1 for outcome in self.applicable if outcome.status == "FAIL")

    @property
    def warnings(self) -> int:
        return sum(1 for outcome in self.applicable if outcome.status == "WARN")

    @property
    def critical_failures(self) -> int:
        return sum(
            1
            for outcome in self.applicable
            if outcome.status == "FAIL" and outcome.severity == "Crítica"
        )

    @property
    def score(self) -> float:
        if not self.applicable:
            return 0.0
        return round(100.0 * self.passed_rules / len(self.applicable), 1)

    @property
    def row_conformity(self) -> float:
        accepted = int(self.metadata.get("rows_accepted", 0) or 0)
        review = int(self.metadata.get("rows_in_review", 0) or 0)
        if accepted <= 0:
            return 0.0
        return round(100.0 * max(accepted - review, 0) / accepted, 1)

    @property
    def global_status(self) -> str:
        if self.critical_failures:
            return "NO APTO"
        if self.failed_rules or self.warnings:
            return "APTO CON OBSERVACIONES"
        return "APTO"

    @property
    def status_class(self) -> str:
        if self.global_status == "NO APTO":
            return "fail"
        if self.global_status == "APTO CON OBSERVACIONES":
            return "warn"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": {
                "score": self.score,
                "row_conformity": self.row_conformity,
                "global_status": self.global_status,
                "rules_total": len(self.outcomes),
                "rules_applicable": len(self.applicable),
                "rules_passed": self.passed_rules,
                "rules_failed": self.failed_rules,
                "warnings": self.warnings,
                "critical_failures": self.critical_failures,
            },
            "metadata": self.metadata,
            "rules": [outcome.to_dict() for outcome in self.outcomes],
        }

    def render_html(self) -> str:
        return _render_html(self)


def _text(series: pd.Series) -> pd.Series:
    return series.map(clean_text)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _safe_index(frame: pd.DataFrame) -> pd.Index:
    return frame.index


def _detail_rows(frame: pd.DataFrame, mask: pd.Series, message: str, limit: int = 8) -> list[str]:
    details: list[str] = []
    if frame.empty:
        return details
    selected = frame.loc[mask.fillna(False)]
    for _, row in selected.head(limit).iterrows():
        ficha = clean_text(row.get("CODIGO_FICHA", "")) or "s/ficha"
        period = clean_text(row.get("ID_PERIODO", "")) or "s/periodo"
        program = clean_text(row.get("NOMBRE_PROGRAMA", ""))[:48]
        suffix = f" · {program}" if program else ""
        details.append(f"Ficha {ficha} · periodo {period}: {message}{suffix}")
    if len(selected) > limit:
        details.append(f"… y {len(selected) - limit} fila(s) más.")
    return details


def _scalar_outcome(
    report: list[RuleOutcome],
    *,
    rule_id: str,
    dimension: str,
    severity: str,
    description: str,
    failed_checks: list[str],
    total: int = 1,
) -> None:
    failed = len(failed_checks)
    if total <= 0:
        status = "NO_EVALUABLE"
    elif failed == 0:
        status = "PASS"
    else:
        status = "FAIL" if severity in {"Crítica", "Alta"} else "WARN"
    report.append(
        RuleOutcome(
            rule_id=rule_id,
            dimension=dimension,
            severity=severity,
            description=description,
            total=total,
            failed=failed,
            status=status,
            details=failed_checks,
        )
    )


def _sum_metric(frame: pd.DataFrame, column: str, parser) -> tuple[float | None, int]:
    if column not in frame.columns:
        return None, 0
    total = 0.0
    valid = 0
    for value in frame[column].tolist():
        parsed, error = parser(value)
        if error is None and parsed is not None:
            total += float(parsed)
            valid += 1
    return total, valid


def build_quality_report(
    curated: pd.DataFrame,
    audit: pd.DataFrame,
    rejected: pd.DataFrame,
    source: pd.DataFrame,
    metadata: dict[str, Any] | Any,
) -> QualityReport:
    """Evalúa las reglas DQ-01..DQ-23 sobre el resultado del ETL.

    Las reglas cubren completitud, unicidad, validez, consistencia,
    integridad referencial y trazabilidad técnica.
    """
    meta = metadata.to_dict() if hasattr(metadata, "to_dict") else dict(metadata)
    outcomes: list[RuleOutcome] = []
    total = len(curated)
    meta.setdefault("unique_fichas", int(curated["CODIGO_FICHA"].nunique()) if total else 0)
    meta.setdefault("total_hours", float(curated["TOTAL_HORAS"].sum()) if total else 0.0)
    meta.setdefault(
        "total_matriculados",
        int(curated["APRENDICES_MATRICULADOS"].sum()) if total else 0,
    )
    meta.setdefault(
        "total_activos",
        int(curated["APRENDICES_ACTIVOS"].sum()) if total else 0,
    )

    uuid = _text(curated["ID_REGISTRO_UUID"]) if "ID_REGISTRO_UUID" in curated else pd.Series(dtype=str)
    period = _numeric(curated["ID_PERIODO"]) if "ID_PERIODO" in curated else pd.Series(dtype=float)
    ficha = _numeric(curated["CODIGO_FICHA"]) if "CODIGO_FICHA" in curated else pd.Series(dtype=float)
    program = _text(curated["NOMBRE_PROGRAMA"]) if "NOMBRE_PROGRAMA" in curated else pd.Series(dtype=str)
    jornada = _numeric(curated["CODIGO_JORNADA"]) if "CODIGO_JORNADA" in curated else pd.Series(dtype=float)
    mat = _numeric(curated["APRENDICES_MATRICULADOS"]) if "APRENDICES_MATRICULADOS" in curated else pd.Series(dtype=float)
    act = _numeric(curated["APRENDICES_ACTIVOS"]) if "APRENDICES_ACTIVOS" in curated else pd.Series(dtype=float)
    can = _numeric(curated["APRENDICES_CANCELADOS"]) if "APRENDICES_CANCELADOS" in curated else pd.Series(dtype=float)
    hours = _numeric(curated["TOTAL_HORAS"]) if "TOTAL_HORAS" in curated else pd.Series(dtype=float)

    def add(
        rule_id: str,
        dimension: str,
        severity: str,
        description: str,
        mask: pd.Series | None = None,
        *,
        denominator: int | None = None,
        details: list[str] | None = None,
    ) -> None:
        if total == 0:
            outcomes.append(
                RuleOutcome(rule_id, dimension, severity, description, 0, 0, "NO_EVALUABLE", [])
            )
            return
        if mask is None:
            outcomes.append(
                RuleOutcome(rule_id, dimension, severity, description, total, 0, "PASS", [])
            )
            return
        clean_mask = mask.reindex(curated.index, fill_value=False).fillna(False).astype(bool)
        failed = int(clean_mask.sum())
        status = "PASS" if failed == 0 else ("FAIL" if severity in {"Crítica", "Alta"} else "WARN")
        payload = details or (
            _detail_rows(curated, clean_mask, "incumple la regla")
            if failed
            else []
        )
        outcomes.append(
            RuleOutcome(
                rule_id=rule_id,
                dimension=dimension,
                severity=severity,
                description=description,
                total=int(denominator if denominator is not None else total),
                failed=failed,
                status=status,
                details=payload,
            )
        )

    # 1. Unicidad de la PK subrogada.
    add(
        "DQ-01",
        "Unicidad",
        "Crítica",
        "ID_REGISTRO_UUID no nulo y único (PK).",
        uuid.eq("") | uuid.duplicated(keep=False),
    )

    # 2. Unicidad del grano ficha-periodo.
    if total:
        duplicate_keys = curated.duplicated(subset=["ID_PERIODO", "CODIGO_FICHA"], keep=False)
    else:
        duplicate_keys = pd.Series(dtype=bool)
    add(
        "DQ-02",
        "Unicidad",
        "Crítica",
        "Una sola fila por (ID_PERIODO, CODIGO_FICHA).",
        duplicate_keys,
    )

    # 3. Dominio de ficha.
    add(
        "DQ-03",
        "Validez",
        "Alta",
        "CODIGO_FICHA entero en el dominio operativo 100000..99999999 (6 a 8 dígitos).",
        ficha.isna() | (ficha < 100_000) | (ficha > 99_999_999),
    )

    # 4. Dominio de periodo.
    add(
        "DQ-04",
        "Validez",
        "Crítica",
        "ID_PERIODO cumple YYYYMM con mes 01..12 y año 2000..2100.",
        period.map(lambda value: parse_period(value) is None if not pd.isna(value) else True),
    )

    # 5. Dominio de jornada.
    add(
        "DQ-05",
        "Validez",
        "Alta",
        "CODIGO_JORNADA pertenece a {1: Diurna, 2: Nocturna, 3: Madrugada, 4: Mixta}.",
        ~jornada.isin([1, 2, 3, 4]),
    )

    # 6. Completitud del programa.
    add(
        "DQ-06",
        "Completitud",
        "Crítica",
        "NOMBRE_PROGRAMA no vacío ni centinela (NOT NULL).",
        program.eq("") | program.str.upper().isin(["NAN", "SIN_NOMBRE", "NONE"]),
    )

    # 7. Métricas no negativas.
    add(
        "DQ-07",
        "Validez",
        "Alta",
        "Conteos de aprendices >= 0.",
        (mat < 0) | (act < 0) | (can < 0),
    )

    # 8. Coherencia activos <= matriculados.
    add(
        "DQ-08",
        "Consistencia",
        "Alta",
        "APRENDICES_ACTIVOS <= APRENDICES_MATRICULADOS.",
        act.gt(mat) & act.notna() & mat.notna(),
    )

    # 9. Horas válidas y cuantizadas.
    add(
        "DQ-09",
        "Validez",
        "Alta",
        "TOTAL_HORAS >= 0, <= Decimal(10,2) y exactamente 2 decimales.",
        hours.isna()
        | (hours < 0)
        | (hours > 99_999_999.99)
        | ((hours - hours.round(2)).abs() > 1e-9),
    )

    # 10. Atomicidad 1FN.
    def atomic_violation(value: Any) -> bool:
        if isinstance(value, (list, tuple, dict, set)):
            return True
        text = clean_text(value)
        return bool(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\r\n]", text))

    atomic_mask = curated.apply(
        lambda row: any(atomic_violation(value) for value in row.tolist()), axis=1
    )
    add(
        "DQ-10",
        "Integridad",
        "Alta",
        "Cada celda es un valor escalar; sin colecciones ni caracteres de control.",
        atomic_mask,
    )

    # 11. Sin celdas compuestas residuales.
    add(
        "DQ-11",
        "Integridad",
        "Alta",
        "NOMBRE_PROGRAMA no conserva el patrón [Ficha]_[Programa]_[Jornada].",
        program.map(has_residual_composite),
    )

    # 12. Sin whitespace ni caracteres invisibles.
    def dirty_text(value: Any) -> bool:
        raw = "" if is_missing(value) else str(value)
        return raw != clean_text(raw) or bool(re.search(r"[\u00a0\ufeff\u200b\u200c\u200d]", raw))

    dirty_mask = curated.apply(
        lambda row: any(dirty_text(value) for value in row.tolist()), axis=1
    )
    add(
        "DQ-12",
        "Integridad",
        "Media",
        "Sin espacios residuales, NBSP, BOM ni caracteres de ancho cero.",
        dirty_mask,
    )

    # 13. Conservación de cardinalidad.
    source_rows = int(meta.get("source_rows_nonblank", 0) or 0)
    accepted = int(meta.get("rows_accepted", 0) or 0)
    rejected_count = int(meta.get("rows_rejected", 0) or 0)
    row_checks: list[str] = []
    if source_rows > 0 and accepted + rejected_count != source_rows:
        row_checks.append(
            f"Filas no vacías={source_rows}, aceptadas={accepted}, rechazadas={rejected_count}; "
            f"faltan {source_rows - accepted - rejected_count}."
        )
    _scalar_outcome(
        outcomes,
        rule_id="DQ-13",
        dimension="Integridad",
        severity="Crítica",
        description="Conservación de cardinalidad: aceptadas + rechazadas = filas no vacías.",
        failed_checks=row_checks,
    )

    # 14. Reconciliación de sumas fuente -> curado (+ rechazados).
    reconciliation_details: list[str] = []
    reconciliation_failed: list[str] = []
    if source_rows > 0:
        for column, parser, tolerance in (
            ("TOTAL_APRENDICES", parse_integer, 0.0),
            ("TOTAL_APRENDICES_ACTIVOS", parse_integer, 0.0),
            ("TOTAL_HORAS", parse_decimal, 0.01 * max(source_rows, 1)),
        ):
            source_sum, _ = _sum_metric(source, column, parser)
            curated_column = {
                "TOTAL_APRENDICES": "APRENDICES_MATRICULADOS",
                "TOTAL_APRENDICES_ACTIVOS": "APRENDICES_ACTIVOS",
                "TOTAL_HORAS": "TOTAL_HORAS",
            }[column]
            curated_sum = float(pd.to_numeric(curated[curated_column], errors="coerce").fillna(0).sum())
            rejected_sum, _ = _sum_metric(rejected, column, parser)
            if source_sum is None:
                continue
            expected = curated_sum + (rejected_sum or 0.0)
            reconciliation_details.append(
                f"{column}: fuente={source_sum:,.2f} · curado={curated_sum:,.2f} · "
                f"rechazados={rejected_sum or 0.0:,.2f} · Δ={source_sum - expected:,.2f}"
            )
            if abs(source_sum - expected) > tolerance + 1e-9:
                reconciliation_failed.append(
                    f"{column}: la suma fuente ({source_sum:,.2f}) no concilia con "
                    f"curado + rechazados ({expected:,.2f})."
                )
        _scalar_outcome(
            outcomes,
            rule_id="DQ-14",
            dimension="Integridad",
            severity="Alta",
            description="Reconciliación de sumas de aprendices y horas entre fuente y curado.",
            failed_checks=reconciliation_failed or [],
            total=1,
        )
        outcomes[-1].details = reconciliation_failed + reconciliation_details
    else:
        outcomes.append(
            RuleOutcome(
                "DQ-14", "Integridad", "Alta", "Reconciliación de sumas fuente -> curado.",
                0, 0, "NO_EVALUABLE", []
            )
        )

    # 15. Consistencia de conteos por sexo en la fuente.
    sex_columns = (
        "TOTAL_APRENDICES_MASCULINOS",
        "TOTAL_APRENDICES_FEMENINOS",
        "TOTAL_APRENDICES_NOBINARIO",
    )
    if all(column in source.columns for column in sex_columns) and "TOTAL_APRENDICES" in source.columns:
        sex_mismatch: list[str] = []
        sex_total = 0
        for position, (_, row) in enumerate(source.iterrows(), start=1):
            total_value, total_error = parse_integer(row.get("TOTAL_APRENDICES"))
            values = [parse_integer(row.get(column)) for column in sex_columns]
            if total_error is not None or any(error is not None for _, error in values):
                continue
            sex_total += 1
            if sum(value or 0 for value, _ in values) != (total_value or 0):
                ficha = clean_text(row.get("IDENTIFICADOR_FICHA")) or "s/ficha"
                sex_mismatch.append(
                    f"Fila {position} · ficha {ficha}: suma por sexo "
                    f"{sum(value or 0 for value, _ in values)} ≠ total {total_value}."
                )
        _scalar_outcome(
            outcomes,
            rule_id="DQ-15",
            dimension="Consistencia",
            severity="Media",
            description="TOTAL_APRENDICES = masculinos + femeninos + no binario (fuente).",
            failed_checks=sex_mismatch,
            total=sex_total,
        )
    else:
        outcomes.append(
            RuleOutcome(
                "DQ-15", "Consistencia", "Media",
                "Conteos por sexo no disponibles en la fuente.", 0, 0, "NO_EVALUABLE", []
            )
        )

    # 16. Delta de redondeo de horas por fila.
    rounding_failures: list[str] = []
    rounding_total = 0
    if "TOTAL_HORAS" in source.columns:
        lookup: dict[tuple[int, int], float] = {}
        for _, row in curated.iterrows():
            key = (int(row["ID_PERIODO"]), int(row["CODIGO_FICHA"]))
            lookup[key] = float(row["TOTAL_HORAS"])
        for position, (_, row) in enumerate(source.iterrows(), start=1):
            source_period = parse_period(row.get("IDPERIODO"))
            source_ficha, ficha_error = parse_integer(row.get("IDENTIFICADOR_FICHA"))
            source_hours, hours_error, _ = quantize_hours(row.get("TOTAL_HORAS"))
            if source_period is None or ficha_error is not None or hours_error is not None:
                continue
            key = (source_period, int(source_ficha))
            if key not in lookup:
                continue
            rounding_total += 1
            if abs(lookup[key] - float(source_hours)) > 0.005 + 1e-9:
                rounding_failures.append(
                    f"Fila {position} · ficha {source_ficha}: fuente {float(source_hours):.4f} "
                    f"vs curado {lookup[key]:.4f}."
                )
        _scalar_outcome(
            outcomes,
            rule_id="DQ-16",
            dimension="Consistencia",
            severity="Media",
            description="El redondeo de TOTAL_HORAS no altera ningún valor en más de 0.005.",
            failed_checks=rounding_failures,
            total=rounding_total,
        )
    else:
        outcomes.append(
            RuleOutcome(
                "DQ-16", "Consistencia", "Media",
                "TOTAL_HORAS no disponible en la fuente.", 0, 0, "NO_EVALUABLE", []
            )
        )

    # 17. Una ficha no puede tener múltiples programas/jornadas en el mismo periodo.
    multi_issues: list[str] = []
    if total:
        grouped = (
            curated.groupby(["ID_PERIODO", "CODIGO_FICHA"], dropna=False)
            .agg(
                programas=("NOMBRE_PROGRAMA", lambda values: len(set(values))),
                jornadas=("CODIGO_JORNADA", lambda values: len(set(values))),
            )
            .reset_index()
        )
        bad = grouped[(grouped["programas"] > 1) | (grouped["jornadas"] > 1)]
        for _, row in bad.head(10).iterrows():
            multi_issues.append(
                f"Ficha {row['CODIGO_FICHA']} · periodo {row['ID_PERIODO']}: "
                f"{row['programas']} programa(s), {row['jornadas']} jornada(s)."
            )
        multi_mask = curated.merge(
            bad[["ID_PERIODO", "CODIGO_FICHA"]],
            on=["ID_PERIODO", "CODIGO_FICHA"],
            how="left",
            indicator=True,
        )["_merge"].eq("both").to_numpy()
    else:
        multi_mask = pd.Series(dtype=bool)
    add(
        "DQ-17",
        "Unicidad",
        "Alta",
        "Una ficha tiene un único programa y una única jornada por periodo.",
        pd.Series(multi_mask, index=curated.index) if total else multi_mask,
        details=multi_issues,
    )

    # 18. Campos obligatorios no nulos.
    required = [
        "ID_REGISTRO_UUID",
        "ID_PERIODO",
        "CODIGO_FICHA",
        "NOMBRE_PROGRAMA",
        "CODIGO_JORNADA",
        "APRENDICES_MATRICULADOS",
        "APRENDICES_ACTIVOS",
        "APRENDICES_CANCELADOS",
        "TOTAL_HORAS",
    ]
    missing_mask = pd.Series(False, index=curated.index)
    for column in required:
        if column not in curated.columns:
            missing_mask = missing_mask | True
            continue
        if column in {"NOMBRE_PROGRAMA", "ID_REGISTRO_UUID"}:
            missing_mask = missing_mask | _text(curated[column]).eq("")
        else:
            missing_mask = missing_mask | _numeric(curated[column]).isna()
    add(
        "DQ-18",
        "Completitud",
        "Crítica",
        "Campos obligatorios del esquema 1FN no nulos.",
        missing_mask,
    )

    # 19. Integridad de artefactos (BOM, hash, sidecar, sin temporales).
    artifact_checks: list[str] = []
    curated_path = Path(str(meta.get("curated_path", "")))
    if meta.get("curated_path") and curated_path.exists():
        data = curated_path.read_bytes()
        if not data.startswith(b"\xef\xbb\xbf"):
            artifact_checks.append("El CSV curado no inicia con BOM UTF-8.")
        expected_hash = meta.get("curated_sha256")
        if expected_hash and sha256_file(curated_path) != expected_hash:
            artifact_checks.append("El SHA-256 del CSV no coincide con la metadata.")
        sidecar = curated_path.with_name(curated_path.name + ".sha256")
        if not sidecar.exists():
            artifact_checks.append("Falta el sidecar .sha256 del CSV curado.")
        else:
            sidecar_hash = sidecar.read_text(encoding="utf-8").split()[0]
            if expected_hash and sidecar_hash != expected_hash:
                artifact_checks.append("El hash del sidecar no coincide con el CSV.")
        temp_files = list(curated_path.parent.glob("*.tmp")) + list(
            curated_path.parent.glob(".*.tmp")
        )
        if temp_files:
            artifact_checks.append(f"Quedaron {len(temp_files)} archivo(s) temporal(es).")
        _scalar_outcome(
            outcomes,
            rule_id="DQ-19",
            dimension="Trazabilidad",
            severity="Media",
            description="BOM UTF-8, SHA-256, sidecar y ausencia de temporales.",
            failed_checks=artifact_checks,
        )
    else:
        outcomes.append(
            RuleOutcome(
                "DQ-19", "Trazabilidad", "Media",
                "Artefactos no disponibles para verificación.", 0, 0, "NO_EVALUABLE", []
            )
        )

    # 20. Cobertura de auditoría de filas REVISAR.
    if total:
        review_mask = _text(curated["ESTADO_REGISTRO"]).eq("REVISAR")
        if not review_mask.any():
            add(
                "DQ-20",
                "Trazabilidad",
                "Media",
                "Toda fila en REVISAR tiene al menos un hallazgo en la auditoría.",
                pd.Series(False, index=curated.index),
            )
        elif not audit.empty and "ID_REGISTRO_UUID" in audit.columns:
            audited = set(_text(audit["ID_REGISTRO_UUID"]))
            uncovered = review_mask & ~uuid.isin(audited)
            add(
                "DQ-20",
                "Trazabilidad",
                "Media",
                "Toda fila en REVISAR tiene al menos un hallazgo en la auditoría.",
                uncovered,
            )
        else:
            add(
                "DQ-20",
                "Trazabilidad",
                "Media",
                "Toda fila en REVISAR tiene al menos un hallazgo en la auditoría.",
                review_mask,
                details=["No hay filas de auditoría para respaldar las filas en REVISAR."],
            )
    else:
        outcomes.append(
            RuleOutcome(
                "DQ-20", "Trazabilidad", "Media",
                "Sin filas para evaluar cobertura de auditoría.", 0, 0, "NO_EVALUABLE", []
            )
        )

    # 21. Idempotencia estructural: UUID recomputado.
    recomputed = []
    for _, row in curated.iterrows():
        recomputed.append(
            make_record_uuid(
                int(row["ID_PERIODO"]),
                int(row["CODIGO_FICHA"]),
                clean_text(row["NOMBRE_PROGRAMA"]).upper(),
                int(row["CODIGO_JORNADA"]),
            )
        )
    add(
        "DQ-21",
        "Trazabilidad",
        "Crítica",
        "El UUID se reconstruye de forma determinista desde la clave natural.",
        uuid.ne(pd.Series(recomputed, index=curated.index)) if total else pd.Series(dtype=bool),
    )

    # 22. Periodo coherente con el nombre del archivo.
    filename_period = period_from_filename(clean_text(meta.get("source_name", "")))
    if filename_period is None:
        outcomes.append(
            RuleOutcome(
                "DQ-22", "Consistencia", "Media",
                "El nombre del archivo no contiene un periodo reconocible.", 0, 0, "NO_EVALUABLE", []
            )
        )
    else:
        _scalar_outcome(
            outcomes,
            rule_id="DQ-22",
            dimension="Consistencia",
            severity="Media",
            description="El periodo del archivo coincide con el periodo del nombre.",
            failed_checks=(
                []
                if int(meta.get("period", 0) or 0) == filename_period
                else [
                    f"Periodo metadata={meta.get('period')} vs nombre={filename_period}."
                ]
            ),
        )

    # 23. Dominio del estado de registro.
    status_series = _text(curated["ESTADO_REGISTRO"]) if "ESTADO_REGISTRO" in curated else pd.Series(dtype=str)
    add(
        "DQ-23",
        "Validez",
        "Media",
        "ESTADO_REGISTRO pertenece a {OK, REVISAR}.",
        ~status_series.isin(["OK", "REVISAR"]),
    )

    return QualityReport(outcomes=outcomes, metadata=meta)


def _badge(status: str) -> str:
    labels = {
        "PASS": "CUMPLE",
        "WARN": "OBSERVACIÓN",
        "FAIL": "NO CUMPLE",
        "NO_EVALUABLE": "NO EVALUABLE",
    }
    return f'<span class="badge {status.lower()}">{labels.get(status, status)}</span>'


def _severity_badge(severity: str) -> str:
    key = {
        "Crítica": "critica",
        "Alta": "alta",
        "Media": "media",
        "Baja": "baja",
    }.get(severity, "media")
    return f'<span class="sev {key}">{html.escape(severity)}</span>'


def _pct_bar(value: float | None, cls: str = "") -> str:
    if value is None:
        return '<span class="muted">—</span>'
    return (
        f'<div class="bar {cls}"><span style="width:{max(0.0, min(100.0, value)):.1f}%"></span></div>'
        f'<span class="pct">{value:.1f}%</span>'
    )


def _render_html(report: QualityReport) -> str:
    esc = lambda value: html.escape(clean_text(value))
    meta = report.metadata
    status = report.global_status
    status_class = report.status_class
    score_color = {"pass": "#7ee2a8", "warn": "#ffd166", "fail": "#ff8a80"}[status_class]

    dimensions: dict[str, list[RuleOutcome]] = {}
    for outcome in report.outcomes:
        dimensions.setdefault(outcome.dimension, []).append(outcome)

    dimension_rows = []
    for dimension, items in dimensions.items():
        applicable = [item for item in items if item.status != "NO_EVALUABLE"]
        passed = sum(1 for item in applicable if item.status == "PASS")
        pct = round(100.0 * passed / len(applicable), 1) if applicable else None
        dimension_rows.append(
            f"<div class='dim'><div class='dim-head'><span>{esc(dimension)}</span>"
            f"<strong>{passed}/{len(applicable)}</strong></div>{_pct_bar(pct)}</div>"
        )

    rule_rows = []
    findings = []
    for outcome in report.outcomes:
        rule_rows.append(
            "<tr>"
            f"<td><code>{esc(outcome.rule_id)}</code></td>"
            f"<td>{esc(outcome.dimension)}</td>"
            f"<td>{_severity_badge(outcome.severity)}</td>"
            f"<td class='desc'>{esc(outcome.description)}</td>"
            f"<td class='num'>{outcome.total:,}</td>"
            f"<td class='num'>{outcome.failed:,}</td>"
            f"<td class='pctcell'>{_pct_bar(outcome.pct)}</td>"
            f"<td>{_badge(outcome.status)}</td>"
            "</tr>"
        )
        if outcome.status in {"FAIL", "WARN"}:
            details = "".join(f"<li>{esc(detail)}</li>" for detail in outcome.details)
            findings.append(
                f"<article class='finding {outcome.status.lower()}'>"
                f"<div class='finding-head'><code>{esc(outcome.rule_id)}</code>"
                f"{_badge(outcome.status)}</div>"
                f"<h3>{esc(outcome.description)}</h3>"
                f"<p class='finding-meta'>{outcome.failed:,} de {outcome.total:,} "
                f"evaluación(es) con hallazgo · severidad {esc(outcome.severity)}.</p>"
                f"<ul>{details or '<li>Sin detalle adicional.</li>'}</ul>"
                "</article>"
            )

    if not findings:
        findings.append(
            "<div class='empty-ok'><strong>Sin hallazgos.</strong> "
            "Todas las reglas evaluables se cumplen.</div>"
        )

    kpis = [
        ("Registros curados", f"{meta.get('rows_accepted', 0):,}", "filas 1FN"),
        ("Fichas únicas", f"{meta.get('unique_fichas', 0):,}", "grano Dim_Ficha"),
        ("Filas conformes", f"{report.row_conformity:.1f}%", "sin observaciones"),
        ("Horas ejecutadas", f"{float(meta.get('total_hours', 0.0)):,.2f}", "TOTAL_HORAS"),
        ("Reglas aprobadas", f"{report.passed_rules}/{len(report.applicable)}", "reglas evaluables"),
        ("Fallos críticos", f"{report.critical_failures}", "requieren acción"),
    ]
    kpi_html = "".join(
        f"<div class='kpi'><span>{esc(label)}</span><strong>{esc(value)}</strong>"
        f"<small>{esc(hint)}</small></div>"
        for label, value, hint in kpis
    )

    recon_rows = [
        ("Filas totales del archivo", f"{meta.get('source_rows_total', 0):,}"),
        ("Filas no vacías bajo cabecera", f"{meta.get('source_rows_nonblank', 0):,}"),
        ("Filas aceptadas", f"{meta.get('rows_accepted', 0):,}"),
        ("Filas rechazadas", f"{meta.get('rows_rejected', 0):,}"),
        ("Filas en REVISAR", f"{meta.get('rows_in_review', 0):,}"),
        ("Filas en blanco omitidas", f"{meta.get('blank_rows_skipped', 0):,}"),
        ("Fila de cabecera (1-based)", f"{int(meta.get('header_row', 0)) + 1}"),
        ("Periodo", f"{meta.get('period', '—')}"),
        ("Origen del periodo", f"{meta.get('period_source', '—')}"),
        ("Codificación / delimitador", f"{meta.get('encoding') or 'XLSX'} / {meta.get('delimiter') or '—'}"),
    ]
    recon_html = "".join(
        f"<tr><td>{esc(label)}</td><td class='num'>{esc(value)}</td></tr>"
        for label, value in recon_rows
    )

    warnings = meta.get("warnings", []) or []
    warnings_html = (
        "<ul class='notes'>" + "".join(f"<li>{esc(item)}</li>" for item in warnings) + "</ul>"
        if warnings
        else "<p class='muted'>Sin advertencias de extracción.</p>"
    )

    curated_path = esc(meta.get("curated_path", "—"))
    audit_path = esc(meta.get("audit_path", "—"))
    metadata_path = esc(meta.get("metadata_path", "—"))
    sha = esc(meta.get("curated_sha256", "—"))
    period_value = meta.get("period", "")
    generated = esc(report.generated_at)

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tablero de Calidad PE-04 · {esc(period_value)}</title>
<style>
:root {{
  --sena:#1f6f43; --sena-dark:#0d3b27; --sena-soft:#e7f1ea; --gold:#f0a500;
  --red:#b42318; --red-soft:#fdecea; --amber:#b54708; --amber-soft:#fff4e5;
  --green:#067647; --green-soft:#e7f6ee; --ink:#15231c; --muted:#617067;
  --line:#dbe5df; --bg:#f3f7f4; --card:#ffffff; --shadow:0 12px 32px rgba(15,61,39,.08);
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:"Segoe UI",Roboto,Helvetica,Arial,sans-serif; color:var(--ink); background:var(--bg); }}
.hero {{ background:linear-gradient(135deg,#0d3b27 0%,#1f6f43 62%,#2f8a58 100%); color:#fff; padding:34px 5vw 30px; }}
.hero-inner {{ max-width:1280px; margin:0 auto; display:flex; gap:32px; align-items:center; justify-content:space-between; flex-wrap:wrap; }}
.eyebrow {{ margin:0 0 8px; text-transform:uppercase; letter-spacing:.16em; font-size:12px; color:#b9e3c9; font-weight:700; }}
h1 {{ margin:0; font-size:clamp(28px,3.3vw,44px); line-height:1.05; letter-spacing:-.02em; }}
.subtitle {{ margin:10px 0 0; color:#d7eee0; font-size:15px; }}
.hero-tags {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:16px; }}
.tag {{ background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2); padding:5px 10px; border-radius:999px; font-size:12px; }}
.ring {{ --score:0; background:conic-gradient({score_color} calc(var(--score) * 1%), rgba(255,255,255,.18) 0); width:156px; height:156px; border-radius:50%; display:grid; place-items:center; flex:none; }}
.ring-inner {{ width:126px; height:126px; border-radius:50%; background:#0d3b27; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; }}
.ring strong {{ font-size:38px; line-height:1; }}
.ring strong span {{ font-size:18px; }}
.ring small {{ color:#b9e3c9; font-size:11px; margin-top:6px; max-width:90px; }}
.status-pill {{ display:inline-block; padding:7px 12px; border-radius:999px; font-size:12px; font-weight:800; letter-spacing:.04em; margin-top:14px; }}
.status-pill.pass {{ background:#d1fadf; color:#054f31; }}
.status-pill.warn {{ background:#fef0c7; color:#7a2e0e; }}
.status-pill.fail {{ background:#fee4e2; color:#7a271a; }}
main {{ max-width:1280px; margin:0 auto; padding:26px 5vw 52px; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-top:-4px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:16px 16px 14px; box-shadow:var(--shadow); }}
.kpi span {{ display:block; color:var(--muted); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; }}
.kpi strong {{ display:block; font-size:26px; margin:6px 0 2px; letter-spacing:-.02em; }}
.kpi small {{ color:var(--muted); font-size:12px; }}
.grid {{ display:grid; grid-template-columns:1.1fr .9fr; gap:16px; margin-top:16px; }}
.panel {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:20px; box-shadow:var(--shadow); margin-top:16px; }}
.grid .panel {{ margin-top:0; }}
h2 {{ margin:0 0 14px; font-size:17px; letter-spacing:-.01em; }}
h3 {{ margin:6px 0 6px; font-size:14px; }}
.muted {{ color:var(--muted); }}
.dim {{ margin:12px 0; }}
.dim-head {{ display:flex; justify-content:space-between; font-size:13px; margin-bottom:6px; }}
.bar {{ background:#edf3ef; height:8px; border-radius:999px; overflow:hidden; display:inline-block; width:calc(100% - 52px); vertical-align:middle; }}
.bar span {{ display:block; height:100%; background:var(--sena); border-radius:999px; }}
.pct {{ display:inline-block; width:44px; text-align:right; font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ text-align:left; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.06em; border-bottom:1px solid var(--line); padding:9px 8px; }}
td {{ border-bottom:1px solid #edf2ef; padding:9px 8px; vertical-align:middle; }}
tr:last-child td {{ border-bottom:0; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
td.desc {{ max-width:430px; }}
td.pctcell {{ min-width:130px; }}
code {{ background:#eef4f0; color:#145235; border-radius:6px; padding:2px 6px; font-size:12px; font-weight:700; }}
.badge {{ display:inline-block; border-radius:999px; padding:3px 9px; font-size:10px; font-weight:800; letter-spacing:.04em; white-space:nowrap; }}
.badge.pass {{ background:var(--green-soft); color:var(--green); }}
.badge.warn {{ background:var(--amber-soft); color:var(--amber); }}
.badge.fail {{ background:var(--red-soft); color:var(--red); }}
.badge.no_evaluable {{ background:#eef2f0; color:#5b6b62; }}
.sev {{ display:inline-block; border-radius:6px; padding:2px 7px; font-size:10px; font-weight:800; }}
.sev.critica {{ background:#fee4e2; color:#912018; }}
.sev.alta {{ background:#fef0c7; color:#7a2e0e; }}
.sev.media {{ background:#e0f2fe; color:#075985; }}
.sev.baja {{ background:#eef2f0; color:#475467; }}
.findings {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }}
.finding {{ border:1px solid var(--line); border-left:4px solid var(--amber); border-radius:12px; padding:14px 16px; background:#fffdf8; }}
.finding.fail {{ border-left-color:var(--red); background:#fffafa; }}
.finding-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; }}
.finding h3 {{ font-size:13px; line-height:1.35; }}
.finding-meta {{ color:var(--muted); font-size:12px; margin:0 0 8px; }}
.finding ul {{ margin:0; padding-left:18px; font-size:12px; color:#34463b; }}
.finding li {{ margin:4px 0; }}
.empty-ok {{ background:var(--green-soft); border:1px solid #b7ebc9; color:#05603a; padding:14px 16px; border-radius:12px; }}
.notes {{ margin:0; padding-left:18px; font-size:13px; color:#4c5f53; }}
.notes li {{ margin:5px 0; }}
.path {{ font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:11px; word-break:break-all; color:#345044; }}
.two-col {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.footer {{ max-width:1280px; margin:0 auto; padding:0 5vw 34px; color:var(--muted); font-size:12px; }}
.footer strong {{ color:#34463b; }}
.method {{ background:#f8fbf9; border:1px dashed #c9d9cf; border-radius:12px; padding:12px 14px; font-size:12px; color:#4a5d51; margin-top:14px; }}
@media (max-width:900px) {{ .grid,.two-col {{ grid-template-columns:1fr; }} .ring {{ width:126px; height:126px; }} .ring-inner {{ width:100px; height:100px; }} .ring strong {{ font-size:30px; }} }}
</style>
</head>
<body>
<header class="hero">
  <div class="hero-inner">
    <div>
      <p class="eyebrow">SENA · Centro ASTIN · ETL-PE04-NORMALIZER</p>
      <h1>Tablero de Calidad de Datos</h1>
      <p class="subtitle">Reporte PE-04 · Ejecución académica y fichas · Periodo {esc(period_value)}</p>
      <div class="hero-tags">
        <span class="tag">Fuente: {esc(meta.get('source_name', '—'))}</span>
        <span class="tag">Cabecera detectada: fila {int(meta.get('header_row', 0)) + 1}</span>
        <span class="tag">1FN · {meta.get('rows_accepted', 0):,} registros</span>
        <span class="tag">UUID v5 determinista</span>
      </div>
      <div class="status-pill {status_class}">{esc(status)}</div>
    </div>
    <div class="ring" style="--score:{report.score:.1f}">
      <div class="ring-inner">
        <strong>{report.score:.0f}<span>%</span></strong>
        <small>reglas aprobadas</small>
      </div>
    </div>
  </div>
</header>
<main>
  <section class="kpis">{kpi_html}</section>

  <section class="grid">
    <div class="panel">
      <h2>Semáforo por dimensión</h2>
      {''.join(dimension_rows) or '<p class="muted">Sin dimensiones evaluables.</p>'}
      <div class="method">
        Lectura: cada barra muestra reglas aprobadas sobre reglas evaluables.
        Un fallo <strong>Crítico</strong> o <strong>Alto</strong> marca la regla como NO CUMPLE;
        un fallo <strong>Medio/Bajo</strong> se reporta como OBSERVACIÓN.
      </div>
    </div>
    <div class="panel">
      <h2>Reconciliación y trazabilidad</h2>
      <table>{recon_html}</table>
      <p class="muted" style="font-size:12px;margin:10px 0 0">Advertencias de extracción:</p>
      {warnings_html}
    </div>
  </section>

  <section class="panel">
    <h2>Matriz de reglas de calidad</h2>
    <div style="overflow-x:auto">
      <table>
        <thead><tr><th>ID</th><th>Dimensión</th><th>Severidad</th><th>Regla</th><th>Evaluados</th><th>Fallidos</th><th>Cumplimiento</th><th>Estado</th></tr></thead>
        <tbody>{''.join(rule_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>Hallazgos y acciones sugeridas</h2>
    <div class="findings">{''.join(findings)}</div>
  </section>

  <section class="panel">
    <h2>Trazabilidad técnica</h2>
    <div class="two-col">
      <div>
        <table>
          <tr><td>Módulo</td><td class="num">{esc(meta.get('module_id', '—'))} v{esc(meta.get('module_version', '—'))}</td></tr>
          <tr><td>Generado (UTC)</td><td class="num">{generated}</td></tr>
          <tr><td>SHA-256 del CSV curado</td><td class="num"><span class="path">{sha}</span></td></tr>
          <tr><td>Namespace UUID</td><td class="num"><span class="path">{esc(meta.get('uuid_namespace', '—'))}</span></td></tr>
          <tr><td>Estrategia de PK</td><td class="num">{esc(meta.get('uuid_strategy', '—'))}</td></tr>
        </table>
      </div>
      <div>
        <table>
          <tr><td>CSV curado</td><td class="num"><span class="path">{curated_path}</span></td></tr>
          <tr><td>Auditoría</td><td class="num"><span class="path">{audit_path}</span></td></tr>
          <tr><td>Metadata</td><td class="num"><span class="path">{metadata_path}</span></td></tr>
          <tr><td>Hash de fuente</td><td class="num"><span class="path">{esc(meta.get('source_sha256', '—'))}</span></td></tr>
        </table>
      </div>
    </div>
    <div class="method">
      <strong>Nota formal sobre idempotencia.</strong> La especificación propone UUID v4,
      pero v4 es aleatorio: dos ejecuciones idénticas producirían PK distintas y violarían
      f(f(x)) = f(x). Este módulo usa UUID v5 (RFC 4122) derivado de la clave natural
      (periodo, ficha, programa, jornada), de modo que la PK es estable y reproducible.
      Si Power BI requiere un UUID v4 visual, puede generarse como columna calculada sin
      afectar la clave de integración.
    </div>
  </section>
</main>
<footer class="footer">
  <strong>ETL-PE04-NORMALIZER</strong> · SENA Centro ASTIN (9217) · Reporte generado automáticamente.
  Las reglas DQ-01..DQ-23 son verificables y se ejecutan en cada corrida.
</footer>
</body>
</html>"""
