# ETL-PE04-NORMALIZER · SENA Centro ASTIN

Normalizador de los reportes mensuales **PE-04 (Ejecución Académica y Fichas)**
a Primera Forma Normal (1FN), con contratos formales, auditoría, idempotencia,
procesamiento por lote y tablero de calidad de datos.

## Estructura

```text
etl_pe04_normalizer.py       CLI del ETL (archivo o carpeta)
pe04_quality_dashboard.py    CLI del tablero de calidad (archivo o carpeta)
pe04_etl/
  contracts.py               {P}, {Q}, {I} y metadata de ejecución
  io.py                      lectura XLSX/CSV/XML y detección de cabecera
  normalize.py               parsing, reglas 1FN y UUID determinista
  persist.py                 escritura atómica y sidecar SHA-256
  pipeline.py                orquestación individual y por lote
  quality.py                 reglas DQ-01..DQ-23 y tablero HTML
tests/                       suite pytest
TEST_TRACEABILITY.md         estrategia y trazabilidad con la especificación
```

## Formatos de origen

El ETL acepta:

| Formato | Extensiones | Observaciones |
|---|---|---|
| Excel moderno | `.xlsx`, `.xlsm` | Lectura con `openpyxl`. |
| Texto plano | `.csv` | Detecta BOM, Latin-1/UTF-8 y delimitadores `;` o `,`. |
| Excel 2003 XML | `.xml` | SpreadsheetML (`urn:schemas-microsoft-com:office:spreadsheet`). |

`TOTAL_HORAS` se interpreta correctamente en sus tres representaciones
habituales de Sofía Plus:

- decimal localizado: `116,5666667`;
- duración de Excel/CSV: `20:00`, `39:50`, `144:00`;
- fecha-hora SpreadsheetML: `1899-12-31T20:00:00.000`, `1900-01-06T00:00:00.000`.

Las duraciones se convierten a horas decimales antes de cuantizar a dos
decimales; `39:50` son 39.83 horas, no 39.50.

## Datos de origen

El repositorio **no incluye** los archivos institucionales ni las salidas
generadas. Para ejecutar el ETL, coloca los reportes en la carpeta `PE04/` o
indica su ruta:

```powershell
# Un solo archivo
python etl_pe04_normalizer.py --source "C:\ruta\PE-04_JUN 2026.xml"

# Todos los archivos de una carpeta (incluye subcarpetas)
python etl_pe04_normalizer.py --source "C:\ruta\PE04"
```

Si existe la carpeta `PE04/` en la raíz del proyecto, es la fuente por defecto;
en caso contrario se usa `PE-04_mayo 2026.xlsx`.

## Ejecución por archivo

```powershell
python etl_pe04_normalizer.py --source "PE-04_JUN 2026.csv" --output data/curated/PE04
python etl_pe04_normalizer.py --source "PE-04_JUN 2026.xml" --period 202606
```

Salidas para un periodo:

```text
data/curated/PE04/202606/pe04_curated_2026_06.csv
data/curated/PE04/202606/pe04_auditoria.csv
data/curated/PE04/202606/pe04_rechazados.csv
data/curated/PE04/202606/pe04_metadata.json
data/curated/PE04/202606/pe04_curated_2026_06.csv.sha256
```

El archivo normalizado incluye el periodo en su nombre con el formato
`pe04_curated_AAAA_MM.csv`; la carpeta de partición conserva `YYYYMM`.

## Ejecución por carpeta

```powershell
python etl_pe04_normalizer.py --source "PE04" --output data/curated/PE04

# Solo el primer nivel, sin subcarpetas
python etl_pe04_normalizer.py --source "PE04" --no-recursive

# Detener el lote en el primer error
python etl_pe04_normalizer.py --source "PE04" --fail-fast
```

Reglas del modo carpeta:

- Cada archivo se procesa de forma independiente y se escribe en su propia
  partición `YYYYMM`.
- El periodo se infiere con prioridad: `--period` (solo archivo individual) >
  preámbulo del reporte > nombre del archivo. Se aceptan meses completos
  (`mayo`, `junio`) y abreviaturas (`MAY`, `JUN`, `SEP`, `DIC`).
- Si dos archivos declaran el mismo periodo, ambos se marcan como `CONFLICT` y
  no se escribe esa partición para evitar sobrescrituras silenciosas.
- Un error en un archivo no detiene el resto del lote, salvo `--fail-fast`.
- Los archivos de salida de ejecuciones anteriores se excluyen del
  descubrimiento para que el lote sea repetible.
- Se genera `pe04_batch_manifest.json` con el estado, periodo y trazabilidad de
  cada archivo.

Salida adicional del lote:

```text
data/curated/PE04/pe04_batch_manifest.json
```

## Tablero de calidad

```powershell
# Un archivo
python pe04_quality_dashboard.py --source "PE-04_JUN 2026.xml"

# Una carpeta completa
python pe04_quality_dashboard.py --source "PE04"
```

En modo carpeta se genera un tablero por periodo y un resumen global:

```text
reports/PE04/202606/pe04_quality_dashboard.html
reports/PE04/202606/pe04_quality_report.json
reports/PE04/pe04_quality_batch.json
```

## Pruebas

```powershell
python -m pytest
python -m pytest -m integration
python -m pytest --cov=pe04_etl --cov-report=term-missing
python -m ruff check .
```

Resultado de referencia:

- **161 pruebas** recolectadas: 156 se ejecutan con datos sintéticos y 5 son de
  integración contra `PE-04_mayo 2026.xlsx` (se omiten si el archivo no está).
- **23 reglas de calidad**, 23 aplicables al XML de junio, 23 aprobadas,
  0 fallos críticos, estado **APTO**.
- XML de junio: 529 registros curados, 0 rechazados, 123 horas redondeadas,
  `TOTAL_HORAS` total = 63,871.69.
- Lote real de `PE04` (junio–septiembre 2026): 4 archivos, 4 periodos,
  2.545 registros curados, 0 rechazados y 4 tableros en estado **APTO**.

## Dependencias

```powershell
python -m pip install -r requirements-dev.txt
```

La aplicación usa `pandas` y `openpyxl`; las pruebas usan `pytest` y
`hypothesis`.
