# ETL-PE04-NORMALIZER · SENA Centro ASTIN

Normalizador del reporte mensual **PE-04 (Ejecución Académica y Fichas)** a
Primera Forma Normal (1FN), con contratos formales, auditoría, idempotencia y
tablero de calidad de datos.

## Estructura

```text
etl_pe04_normalizer.py       CLI del ETL
pe04_quality_dashboard.py    CLI del tablero de calidad
pe04_etl/
  contracts.py               {P}, {Q}, {I} y metadata de ejecución
  io.py                      lectura XLSX/CSV y detección de cabecera
  normalize.py               parsing, reglas 1FN y UUID determinista
  persist.py                 escritura atómica y sidecar SHA-256
  pipeline.py                orquestación
  quality.py                 reglas DQ-01..DQ-23 y tablero HTML
tests/                       suite pytest (117 pruebas)
TEST_TRACEABILITY.md         estrategia y trazabilidad con la especificación
```

## Datos de origen

El repositorio **no incluye** el archivo institucional
`PE-04_mayo 2026.xlsx` ni las salidas generadas. Para ejecutar el ETL, coloca
el archivo fuente en la raíz del proyecto o indica su ruta:

```powershell
python etl_pe04_normalizer.py --source "C:\ruta\PE-04_mayo 2026.xlsx"
```

Las pruebas de integración contra el archivo real se omiten automáticamente si
el XLSX no está presente; las demás pruebas usan datos sintéticos.

## Ejecución

```powershell
python etl_pe04_normalizer.py
python pe04_quality_dashboard.py
```

Salidas:

```text
data/curated/PE04/202605/pe04_curated.csv
data/curated/PE04/202605/pe04_auditoria.csv
data/curated/PE04/202605/pe04_rechazados.csv
data/curated/PE04/202605/pe04_metadata.json
reports/PE04/202605/pe04_quality_dashboard.html
reports/PE04/202605/pe04_quality_report.json
```

## Pruebas

```powershell
python -m pytest
python -m pytest -m integration
python -m pytest --cov=pe04_etl --cov-report=term-missing
```

Resultado de referencia: **117 pruebas aprobadas**, **92 % de cobertura** y
**23/23 reglas de calidad** aprobadas sobre `PE-04_mayo 2026.xlsx`.

## Dependencias

```powershell
python -m pip install -r requirements-dev.txt
```

La aplicación usa `pandas` y `openpyxl`; las pruebas usan `pytest` y
`hypothesis`.
