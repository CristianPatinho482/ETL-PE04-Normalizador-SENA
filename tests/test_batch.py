from __future__ import annotations

import json
from pathlib import Path

import pytest

from pe04_etl.contracts import ContractViolation
from pe04_etl.io import discover_sources
from pe04_etl.pipeline import run_batch
from tests.conftest import BASE_COLUMNS, sample_row, write_csv, write_excel_xml

NO_IDPERIODO_COLUMNS = [column for column in BASE_COLUMNS if column != "IDPERIODO"]


def _period_csv(path: Path, period: int, ficha: int) -> Path:
    return write_csv(
        path,
        [sample_row(IDENTIFICADOR_FICHA=ficha, IDPERIODO=period)],
        header_row=4,
        preamble=["PE-04_", "", str(period)],
    )


def test_discover_sources_filters_ghosts_unsupported_and_output(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    (source / "~$PE-04_ENERO 2026.csv").write_text("fantasma", encoding="utf-8")
    (source / "nota.txt").write_text("no es PE-04", encoding="utf-8")

    output = tmp_path / "out"
    output.mkdir()
    _period_csv(output / "generado.csv", 202602, 3100002)

    files = discover_sources(source, exclude_dirs=(output,))
    assert [path.name for path in files] == ["PE-04_ENERO 2026.csv"]


def test_run_batch_processes_each_period_in_its_partition(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    nested = source / "lote"
    nested.mkdir()
    _period_csv(nested / "PE-04_FEBRERO 2026.csv", 202602, 3100002)

    output = tmp_path / "out"
    batch = run_batch(source, output)

    assert batch.files_total == 2
    assert batch.periods == [202601, 202602]
    assert [item.status for item in batch.items] == ["OK", "OK"]
    for period in (202601, 202602):
        partition = output / str(period)
        assert (partition / "pe04_curated.csv").exists()
        assert (partition / "pe04_auditoria.csv").exists()
        assert (partition / "pe04_metadata.json").exists()

    assert batch.manifest_path is not None
    manifest = json.loads(Path(batch.manifest_path).read_text(encoding="utf-8"))
    assert manifest["files_total"] == 2
    assert manifest["files_ok"] == 2
    assert manifest["periods"] == [202601, 202602]
    assert {item["source"] for item in manifest["items"]} == {
        "PE-04_ENERO 2026.csv",
        "PE-04_FEBRERO 2026.csv",
    }


def test_run_batch_non_recursive_ignores_subfolders(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    nested = source / "lote"
    nested.mkdir()
    _period_csv(nested / "PE-04_FEBRERO 2026.csv", 202602, 3100002)

    batch = run_batch(source, tmp_path / "out", recursive=False)
    assert batch.files_total == 1
    assert batch.periods == [202601]


def test_duplicate_period_marks_conflict_and_skips_partition(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    _period_csv(source / "PE-04_202601.csv", 202601, 3100002)

    output = tmp_path / "out"
    batch = run_batch(source, output)

    assert batch.files_total == 2
    assert len(batch.conflict_items) == 2
    assert batch.periods == []
    assert not (output / "202601" / "pe04_curated.csv").exists()
    assert all("duplicado" in (item.error or "") for item in batch.conflict_items)

    manifest = json.loads(Path(batch.manifest_path).read_text(encoding="utf-8"))
    assert manifest["files_conflict"] == 2
    assert manifest["files_ok"] == 0


def test_run_batch_continues_after_a_broken_file(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    broken = source / "PE-04_202602.csv"
    broken.write_text("CODIGO_REGIONAL;NOMBRE_REGIONAL\n1;SIN CABECERA PE-04\n", encoding="utf-8")

    output = tmp_path / "out"
    batch = run_batch(source, output)

    statuses = {item.source.name: item.status for item in batch.items}
    assert statuses == {
        "PE-04_202602.csv": "ERROR",
        "PE-04_ENERO 2026.csv": "OK",
    }
    assert batch.periods == [202601]
    assert (output / "202601" / "pe04_curated.csv").exists()
    assert batch.has_errors is True


def test_run_batch_fail_fast_stops_after_first_error(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    broken = source / "PE-04_202601.csv"
    broken.write_text("CODIGO_REGIONAL;NOMBRE_REGIONAL\n1;SIN CABECERA PE-04\n", encoding="utf-8")
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)

    batch = run_batch(source, tmp_path / "out", fail_fast=True)
    assert batch.files_total == 2
    assert len(batch.items) == 1
    assert batch.items[0].status == "ERROR"


def test_run_batch_empty_folder_violates_precondition(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    with pytest.raises(ContractViolation) as error:
        run_batch(source, tmp_path / "out")
    assert error.value.clause == "P"
    assert "No se encontraron archivos PE-04" in error.value.message


def test_run_batch_excludes_previous_outputs_from_next_run(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    output = tmp_path / "out"

    first = run_batch(source, output)
    curated_path = output / "202601" / "pe04_curated.csv"
    first_bytes = curated_path.read_bytes()
    second = run_batch(source, output)

    assert first.files_total == 1
    assert second.files_total == 1
    assert second.periods == [202601]
    assert all(item.status == "OK" for item in second.items)
    assert curated_path.read_bytes() == first_bytes


def test_run_batch_accepts_xml_and_csv_together(tmp_path):
    source = tmp_path / "PE04"
    source.mkdir()
    _period_csv(source / "PE-04_ENERO 2026.csv", 202601, 3100001)
    write_excel_xml(
        source / "PE-04_JUN 2026.xml",
        [
            sample_row(
                IDENTIFICADOR_FICHA=3533390,
                TOTAL_APRENDICES=13,
                TOTAL_HORAS="1899-12-31T20:00:00.000",
            )
        ],
        header_row=4,
        preamble=["PE-04_", "", "202606"],
        columns=NO_IDPERIODO_COLUMNS,
    )

    batch = run_batch(source, tmp_path / "out")
    assert batch.periods == [202601, 202606]
    assert [item.status for item in batch.items] == ["OK", "OK"]
