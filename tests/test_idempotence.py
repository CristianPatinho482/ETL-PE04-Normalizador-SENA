from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pe04_etl.contracts import ProcessingMetadata
from pe04_etl.normalize import (
    CURATED_COLUMNS,
    canonicalize_curated,
    normalize_dataframe,
)
from pe04_etl.pipeline import run_pipeline
from tests.conftest import sample_row, write_xlsx


def test_rerun_is_byte_identical(tmp_path, source_two_rows):
    first = run_pipeline(source_two_rows, tmp_path / "run_a")
    second = run_pipeline(source_two_rows, tmp_path / "run_b")
    first_csv = Path(first.paths["curated"]).read_bytes()
    second_csv = Path(second.paths["curated"]).read_bytes()
    assert first_csv == second_csv
    assert first.metadata.curated_sha256 == second.metadata.curated_sha256
    assert first_csv.startswith(b"\xef\xbb\xbf")


def test_canonicalize_curated_is_identity(source_two_rows):
    result = run_pipeline(source_two_rows)
    once = canonicalize_curated(result.curated)
    twice = canonicalize_curated(once)
    pd.testing.assert_frame_equal(once, result.curated, check_dtype=False)
    pd.testing.assert_frame_equal(twice, once, check_dtype=False)


def test_uuid_is_stable_under_whitespace_and_case_variants(tmp_path):
    path_a = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row(NOMBRE_PROGRAMA_FORMACION="  aplicación   de procesos ")],
    )
    path_b = write_xlsx(
        tmp_path / "PE-04_mayo 2026 (copia).xlsx",
        [sample_row(NOMBRE_PROGRAMA_FORMACION="APLICACIÓN DE PROCESOS")],
    )
    result_a = run_pipeline(path_a)
    result_b = run_pipeline(path_b)
    assert result_a.curated.iloc[0]["ID_REGISTRO_UUID"] == result_b.curated.iloc[0]["ID_REGISTRO_UUID"]
    assert result_a.curated.iloc[0]["NOMBRE_PROGRAMA"] == "APLICACIÓN DE PROCESOS"


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    ficha=st.integers(min_value=100_000, max_value=99_999_999),
    programa=st.text(
        alphabet="ABCDEFGHIJKLMNÑOPQRSTUVWXYZ abcdefghijklmnñopqrstuvwxyzáéíóúÁÉÍÓÚ",
        min_size=1,
        max_size=40,
    ).map(lambda value: " ".join(value.split())).filter(bool),
    jornada=st.integers(min_value=1, max_value=4),
    matriculados=st.integers(min_value=0, max_value=1000),
    activos=st.integers(min_value=0, max_value=1000),
    cancelados=st.integers(min_value=0, max_value=1000),
    horas=st.decimals(min_value=Decimal(0), max_value=Decimal(10000), places=2),
)
def test_property_normalization_is_idempotent(
    ficha, programa, jornada, matriculados, activos, cancelados, horas
):
    frame = pd.DataFrame(
        [
            {
                "IDENTIFICADOR_FICHA": ficha,
                "NOMBRE_PROGRAMA_FORMACION": programa,
                "CODIGO_JORNADA": jornada,
                "NOMBRE_JORNADA": {1: "DIURNA", 2: "NOCTURNA", 3: "MADRUGADA", 4: "MIXTA"}[jornada],
                "TOTAL_APRENDICES": matriculados,
                "TOTAL_APRENDICES_ACTIVOS": activos,
                "TOTAL_APRENDICES_CANCELADOS": cancelados,
                "TOTAL_HORAS": horas,
                "IDPERIODO": 202605,
            }
        ]
    )
    result = normalize_dataframe(
        frame,
        metadata=ProcessingMetadata(source_name="propiedad.xlsx"),
        period_hint=202605,
    )
    canonical = canonicalize_curated(result.curated)
    pd.testing.assert_frame_equal(canonical, result.curated, check_dtype=False)
    assert list(result.curated.columns) == CURATED_COLUMNS
    assert result.curated.iloc[0]["TOTAL_HORAS"] == float(horas)
