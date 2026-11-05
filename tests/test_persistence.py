from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from pe04_etl.persist import _replace_with_retry, curated_filename, write_curated_csv
from pe04_etl.pipeline import run_pipeline
from tests.conftest import BASE_COLUMNS, sample_row, write_xlsx


def test_output_is_partitioned_with_bom_and_sidecar(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    path = Path(result.paths["curated"])
    assert path.parent.name == "202605"
    assert path.name == "pe04_curated_2026_05.csv"
    assert path.name == curated_filename(202605)
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert b";" in data
    sidecar = path.with_name(path.name + ".sha256")
    assert sidecar.exists()
    assert sidecar.read_text(encoding="utf-8").split()[0] == result.metadata.curated_sha256


def test_output_hours_have_exactly_two_decimals(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    path = Path(result.paths["curated"])
    text = path.read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line]
    header = lines[0].split(";")
    hours_index = header.index("TOTAL_HORAS")
    for line in lines[1:]:
        value = line.split(";")[hours_index]
        assert value.count(".") == 1
        assert len(value.split(".")[1]) == 2


def test_semicolons_inside_program_are_quoted(tmp_path):
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row(NOMBRE_PROGRAMA_FORMACION="PROGRAMA; CON PUNTO Y COMA")],
    )
    result = run_pipeline(path, tmp_path / "curated")
    csv_path = Path(result.paths["curated"])
    frame = pd.read_csv(csv_path, sep=";", encoding="utf-8-sig", dtype=str)
    assert frame.iloc[0]["NOMBRE_PROGRAMA"] == "PROGRAMA; CON PUNTO Y COMA"
    assert len(frame) == 1


def test_atomic_write_preserves_previous_file_if_serialization_fails(
    tmp_path, monkeypatch, source_two_rows
):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    target = Path(result.paths["curated"])
    original = target.read_bytes()

    def explode(*args, **kwargs):
        raise OSError("fallo simulado de serialización")

    monkeypatch.setattr(pd.DataFrame, "to_csv", explode)
    with pytest.raises(OSError):
        write_curated_csv(result.curated, tmp_path / "curated", 202605)
    assert target.read_bytes() == original
    assert not list(target.parent.glob(".*.tmp"))


def test_atomic_write_cleans_temp_if_rename_fails(tmp_path, monkeypatch, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    target = Path(result.paths["curated"])
    original = target.read_bytes()

    def explode(*args, **kwargs):
        raise OSError("fallo simulado de rename")

    monkeypatch.setattr("pe04_etl.persist.os.replace", explode)
    with pytest.raises(OSError):
        write_curated_csv(result.curated, tmp_path / "curated", 202605)
    assert target.read_bytes() == original
    assert not list(target.parent.glob(".*.tmp"))


def test_replace_retries_when_file_is_temporarily_locked(tmp_path, monkeypatch):
    source = tmp_path / "origen.tmp"
    destination = tmp_path / "destino.csv"
    source.write_text("nuevo", encoding="utf-8")
    destination.write_text("anterior", encoding="utf-8")
    import pe04_etl.persist as persist_module

    real_replace = persist_module.os.replace
    calls = {"count": 0}

    def flaky_replace(src, dst):
        calls["count"] += 1
        if calls["count"] < 3:
            raise PermissionError(13, "archivo bloqueado")
        return real_replace(src, dst)

    monkeypatch.setattr(persist_module.os, "replace", flaky_replace)
    monkeypatch.setattr(persist_module.time, "sleep", lambda _: None)
    _replace_with_retry(source, destination)
    assert calls["count"] == 3
    assert destination.read_text(encoding="utf-8") == "nuevo"
    assert not source.exists()


def test_period_can_be_derived_from_filename(tmp_path):
    columns = [column for column in BASE_COLUMNS if column != "IDPERIODO"]
    path = write_xlsx(
        tmp_path / "PE-04_mayo 2026.xlsx",
        [sample_row()],
        columns=columns,
    )
    result = run_pipeline(path, tmp_path / "curated")
    assert result.metadata.period == 202605
    assert result.metadata.period_source == "nombre_archivo"
    assert Path(result.paths["curated"]).parent.name == "202605"


def test_metadata_json_is_valid_and_traceable(tmp_path, source_two_rows):
    result = run_pipeline(source_two_rows, tmp_path / "curated")
    metadata_path = Path(result.paths["metadata"])
    assert metadata_path.exists()
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["period"] == 202605
    assert payload["rows_accepted"] == 2
    assert payload["curated_sha256"] == result.metadata.curated_sha256
