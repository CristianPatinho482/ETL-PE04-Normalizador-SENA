from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from pe04_etl.contracts import ContractViolation
from pe04_etl.io import extract_pe04, read_raw_table
from pe04_etl.pipeline import run_pipeline
from pe04_etl.quality import build_quality_report
from tests.conftest import BASE_COLUMNS, sample_row, write_csv, write_excel_xml

NO_IDPERIODO_COLUMNS = [column for column in BASE_COLUMNS if column != "IDPERIODO"]


def _xml_fixture(tmp_path: Path, *, with_period: bool = True) -> Path:
    rows = [
        sample_row(
            IDENTIFICADOR_FICHA=3533390,
            TOTAL_APRENDICES=13,
            TOTAL_HORAS="1899-12-31T20:00:00.000",
        ),
        sample_row(
            IDENTIFICADOR_FICHA=3533391,
            TOTAL_APRENDICES=25,
            TOTAL_HORAS="1900-01-06T00:00:00.000",
        ),
    ]
    preamble = ["PE-04_", "", "202606"] if with_period else ["PE-04_"]
    return write_excel_xml(
        tmp_path / "PE-04_JUN 2026.xml",
        rows,
        header_row=4,
        preamble=preamble,
        columns=NO_IDPERIODO_COLUMNS,
    )


def test_excel_xml_is_read_with_header_and_shape(tmp_path):
    path = _xml_fixture(tmp_path)
    raw = read_raw_table(path)
    assert raw.kind == "xml"
    assert raw.sheet_name == "Reporte"
    assert raw.frame.shape[0] == 7
    assert raw.frame.shape[1] == len(NO_IDPERIODO_COLUMNS)

    extracted = extract_pe04(raw)
    assert extracted.header_row == 4
    assert len(extracted.columns) == len(NO_IDPERIODO_COLUMNS)
    assert len(extracted.frame) == 2


def test_excel_xml_pipeline_infers_period_from_preamble(tmp_path):
    result = run_pipeline(_xml_fixture(tmp_path))
    assert result.metadata.period == 202606
    assert result.metadata.period_source == "preambulo"
    assert result.metadata.rows_accepted == 2
    assert result.metadata.rows_rejected == 0

    hours = {
        int(row["CODIGO_FICHA"]): float(row["TOTAL_HORAS"])
        for _, row in result.curated.iterrows()
    }
    assert hours == {3533390: 20.0, 3533391: 144.0}


def test_excel_xml_matches_equivalent_csv_identifiers(tmp_path):
    xml_result = run_pipeline(_xml_fixture(tmp_path))
    xml_uuid = xml_result.curated.set_index("CODIGO_FICHA")["ID_REGISTRO_UUID"].to_dict()

    csv_path = write_csv(
        tmp_path / "PE-04_JUN 2026.csv",
        [
            sample_row(
                IDENTIFICADOR_FICHA=3533390,
                TOTAL_APRENDICES=13,
                TOTAL_HORAS="20:00",
            ),
            sample_row(
                IDENTIFICADOR_FICHA=3533391,
                TOTAL_APRENDICES=25,
                TOTAL_HORAS="144:00",
            ),
        ],
        header_row=4,
        preamble=["PE-04_", "", "202606"],
        columns=NO_IDPERIODO_COLUMNS,
    )
    csv_result = run_pipeline(csv_path)
    csv_uuid = csv_result.curated.set_index("CODIGO_FICHA")["ID_REGISTRO_UUID"].to_dict()
    assert xml_uuid == csv_uuid
    pd.testing.assert_frame_equal(xml_result.curated, csv_result.curated)


def test_excel_xml_quality_dq14_reconciles_durations(tmp_path):
    result = run_pipeline(_xml_fixture(tmp_path))
    report = build_quality_report(
        result.curated,
        result.audit,
        result.rejected,
        result.extract.frame,
        result.metadata.to_dict(),
    )
    dq14 = next(outcome for outcome in report.outcomes if outcome.rule_id == "DQ-14")
    dq16 = next(outcome for outcome in report.outcomes if outcome.rule_id == "DQ-16")
    assert dq14.status == "PASS"
    assert dq16.status == "PASS"
    assert report.critical_failures == 0
    assert report.global_status == "APTO"


def test_excel_xml_without_preamble_falls_back_to_filename(tmp_path):
    result = run_pipeline(_xml_fixture(tmp_path, with_period=False))
    assert result.metadata.period == 202606
    assert result.metadata.period_source == "nombre_archivo"


def test_excel_xml_respects_row_cell_index_and_merge_attributes(tmp_path):
    path = tmp_path / "PE-04_202606.xml"
    path.write_text(
        """<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Reporte">
  <Table ss:ExpandedColumnCount="5" ss:ExpandedRowCount="2">
   <Row><Cell><Data ss:Type="String">A</Data></Cell></Row>
   <Row ss:Index="2">
    <Cell ss:Index="3"><Data ss:Type="String">X</Data></Cell>
    <Cell ss:MergeAcross="1"><Data ss:Type="String">Y</Data></Cell>
   </Row>
  </Table>
 </Worksheet>
</Workbook>
""",
        encoding="utf-8",
    )
    raw = read_raw_table(path)
    assert raw.frame.shape == (2, 5)
    assert raw.frame.iloc[0].tolist() == ["A", None, None, None, None]
    assert raw.frame.iloc[1].tolist() == [None, None, "X", "Y", None]


def test_invalid_excel_xml_is_reported_as_contract_violation(tmp_path):
    path = tmp_path / "PE-04_202606.xml"
    path.write_text("<Workbook><Row>", encoding="utf-8")
    with pytest.raises(ContractViolation) as error:
        read_raw_table(path)
    assert error.value.clause == "P"
    assert "XML inválido" in error.value.message
