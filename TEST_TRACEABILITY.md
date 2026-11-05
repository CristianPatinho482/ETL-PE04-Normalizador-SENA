# Estrategia de pruebas — ETL-PE04-NORMALIZER

Este documento responde a la pregunta: **¿cómo probar el normalizador PE-04?**
La suite implementada combina pruebas unitarias, de contrato, de integración,
basadas en propiedades y de regresión, además de un tablero de calidad que
convierte las reglas de validación en evidencia visual.

## 1. Pirámide de pruebas implementada

| Nivel | Qué verifica | Herramienta | Archivo |
|---|---|---|---|
| Unitarias | Parsing de celdas compuestas, números localizados, duraciones de Excel, periodos, jornadas, limpieza de texto | `pytest` | `tests/test_parsing.py` |
| Contrato (Hoare) | Precondiciones `{P}`, postcondiciones `{Q}` e invariantes del resultado | `pytest` + `ContractViolation` | `tests/test_contracts.py` |
| Integración | XLSX/CSV/XML reales y sintéticos, cabeceras desplazadas, BOM, Latin-1, filas en blanco | `pytest`, `openpyxl`, `pandas` | `tests/test_header_detection.py`, `tests/test_xml_source.py`, `tests/test_integration_real_file.py` |
| Lote | Descubrimiento de archivos, particiones por periodo, conflictos, `--fail-fast` y manifiesto | `pytest` | `tests/test_batch.py` |
| Propiedades | Idempotencia `f(f(x)) = f(x)`, estabilidad del UUID, dominios | `hypothesis` | `tests/test_idempotence.py` |
| Regresión | Misma entrada ⇒ mismos bytes de salida y mismo SHA-256 | `pytest` | `tests/test_idempotence.py`, `tests/test_persistence.py` |
| Persistencia | Escritura atómica, `os.replace`, BOM, sidecar SHA-256, cuarentena de temporales | `pytest` + `monkeypatch` | `tests/test_persistence.py` |
| Calidad de datos | Reglas DQ-01..DQ-23 y tablero HTML | `pytest` | `tests/test_quality.py`, `tests/test_edge_cases.py` |

## 2. Matriz de trazabilidad con la especificación

| Caso de la especificación | Escenario | Prueba implementada |
|---|---|---|
| TC-PE04-01 | Happy Path: cabecera en fila 0 y fichas válidas | `test_detects_header_in_every_allowed_row[0]`, `test_postconditions_hold_on_clean_source`, `test_real_pe04_file_end_to_end` |
| TC-PE04-02 | Cabecera desplazada a filas 1–4 | `test_detects_header_in_every_allowed_row[1..4]`, `test_header_beyond_row_four_is_rejected` |
| TC-PE04-03 | Celdas compuestas `Ficha_Programa_Jornada` | `test_composite_cell_is_split_into_atoms`, `test_composite_cell_accepts_underscores_inside_program`, `test_composite_cell_precedes_column_and_flags_inconsistency` |
| TC-PE04-04 | Comas decimales, NBSP, nulos y textos numéricos | `test_parse_decimal_localized_formats`, `test_parse_integer_accepts_thousands_and_integral_floats`, `test_quantize_hours_half_up_to_two_decimals`, `test_invalid_metrics_are_zeroed_and_audited` |
| — | Idempotencia fuerte | `test_rerun_is_byte_identical`, `test_canonicalize_curated_is_identity`, `test_property_normalization_is_idempotent` |
| — | Rechazo y auditoría de filas inválidas | `test_invalid_rows_are_rejected_and_audited`, `test_malformed_composite_cell_is_rejected_not_silently_split` |
| — | Fichas duplicadas / grano dimensional | `test_duplicate_ficha_is_a_critical_failure`, `test_dq17_detects_same_ficha_with_two_programs` |
| — | Persistencia atómica | `test_atomic_write_preserves_previous_file_if_serialization_fails`, `test_atomic_write_cleans_temp_if_rename_fails` |
| — | Reconciliación fuente → curado | `test_real_pe04_sums_reconcile_with_source`, `test_dq14_detects_tampered_aggregate` |
| — | XML SpreadsheetML 2003 | `test_excel_xml_is_read_with_header_and_shape`, `test_excel_xml_pipeline_infers_period_from_preamble`, `test_excel_xml_quality_dq14_reconciles_durations` |
| — | Duraciones de Excel (`HH:MM`, `HHH:MM`, ISO) | `test_parse_hours_accepts_excel_and_csv_durations`, `test_parse_hours_converts_minutes_not_centesimal_hours`, `test_parse_hours_rejects_invalid_or_non_duration_values` |
| — | Periodo por preámbulo y abreviaturas | `test_period_from_preamble_reads_period_before_header`, `test_period_from_filename[JUN/ENERO/SEP/DIC]` |
| — | Lote multiperiodo en particiones `YYYYMM` | `test_run_batch_processes_each_period_in_its_partition`, `test_run_batch_non_recursive_ignores_subfolders` |
| — | Conflicto por periodo duplicado | `test_duplicate_period_marks_conflict_and_skips_partition` |
| — | Tolerancia a errores y `--fail-fast` | `test_run_batch_continues_after_a_broken_file`, `test_run_batch_fail_fast_stops_after_first_error` |
| — | Carpeta vacía y exclusión de salidas previas | `test_run_batch_empty_folder_violates_precondition`, `test_run_batch_excludes_previous_outputs_from_next_run` |

## 3. Comandos

```powershell
# Suite completa (161 pruebas recolectadas)
python -m pytest

# Solo integración contra el archivo real
python -m pytest -m integration

# Cobertura
python -m pytest --cov=pe04_etl --cov-report=term-missing

# Ejecutar el ETL por archivo o por carpeta
python etl_pe04_normalizer.py
python etl_pe04_normalizer.py --source "PE04"

# Generar el tablero de calidad por archivo o por carpeta
python pe04_quality_dashboard.py
python pe04_quality_dashboard.py --source "PE04"
```

Resultado de referencia:

- 161 pruebas recolectadas; 156 con datos sintéticos y 5 de integración que se
  omiten automáticamente si `PE-04_mayo 2026.xlsx` no está en la raíz.
- Cobertura de `pe04_etl`: 91 % (líneas).
- 453 registros curados desde `PE-04_mayo 2026.xlsx`, 0 rechazados, 109 horas
  redondeadas de 6 a 2 decimales; 23/23 reglas aprobadas.
- 529 registros curados desde `PE-04_JUN 2026.xml`, 0 rechazados, 123 horas
  redondeadas; 23/23 reglas aprobadas, estado APTO.
- El XML y el CSV de junio producen exactamente los mismos UUID y el mismo
  total de horas (63,871.69), lo que confirma la equivalencia entre formatos.
- Lote real de `PE04` (junio–septiembre 2026): 4 archivos, 4 periodos,
  2.545 registros curados, 0 rechazados, 0 conflictos y 4 tableros APTO.

## 4. Qué cubre cada tipo de prueba

### 4.1 Unitarias

Se prueban funciones puras sin filesystem ni estado global:

- `parse_decimal`: `"116,5666667"`, `"1.234,56"`, `"(1.234,56)"`, `"N/A"`.
- `parse_integer`: `"1.234"` → `1234`, `"26.0"` → `26`, `"116,56"` → error.
- `parse_hours`: `"20:00"` → `20`, `"144:00"` → `144`, `"39:50"` → `39.83`,
  `"1899-12-31T20:00:00.000"` → `20`, `timedelta(hours=144)` → `144`.
- `quantize_hours`: `"1,005"` → `1.01` con `ROUND_HALF_UP`; una duración
  `"39:50"` se cuantiza a `39.83` y se marca como redondeada.
- `parse_composite`: mayúsculas, NBSP, guiones bajos internos y formatos inválidos.
- `parse_period`: `202605`, `2026-05`, `05/2026`, meses inválidos.
- `period_from_filename`: `PE-04_mayo 2026.xlsx` → `202605`,
  `PE-04_JUN 2026.xml` → `202606`.
- `period_from_preamble`: recupera `202606` de una celda suelta antes de la
  cabecera y no confunde fichas de 6–8 dígitos con periodos.

### 4.2 Contrato

Cada ejecución verifica:

- `{P}` archivo existente, no fantasma (`~$`), extensión soportada, cabecera
  reconocible y al menos una fila de datos.
- `{Q}` PK única, ficha positiva, periodo `YYYYMM`, jornada en `{1,2,3,4}`,
  métricas `>= 0`, horas con 2 decimales y conservación de cardinalidad
  (`aceptadas + rechazadas = filas no vacías`).
- `{I}` toda fila termina aceptada o rechazada, y cada fila `REVISAR` tiene
  al menos un hallazgo en la auditoría.

### 4.3 Integración

Los fixtures generan XLSX/CSV/XML reales con `openpyxl`, `csv` y
`xml.sax.saxutils`, incluyendo:

- Cabecera en filas 0, 1, 2, 3 y 4.
- Preámbulos institucionales con una firma suelta para evitar falsos positivos.
- BOM UTF-8, Latin-1/CP1252 y delimitadores `;` y `,`.
- Filas completamente vacías y columnas duplicadas.
- Libros SpreadsheetML 2003 con hoja `Reporte`, celdas `ss:Type="DateTime"` y
  periodos en el preámbulo.
- Carpetas con varios periodos, subcarpetas, archivos fantasma `~$...`,
  archivos corruptos y periodos duplicados.

### 4.4 Propiedades (Hypothesis)

Se generan aleatoriamente fichas, programas, jornadas, conteos y horas. Para
cada caso se verifica:

```text
f(f(x)) = f(x)
```

Es decir, normalizar un registro ya normalizado no altera ningún atributo del
esquema 1FN ni el UUID. También se comprueba que el UUID es estable ante
variaciones de espacios y mayúsculas en el nombre del programa.

### 4.5 Regresión y persistencia

- Dos corridas consecutivas producen el **mismo CSV byte a byte**.
- El CSV inicia con BOM UTF-8 y usa `;`.
- `TOTAL_HORAS` se serializa siempre con exactamente 2 decimales.
- El sidecar `.sha256` coincide con el hash del CSV.
- Si falla la serialización o el `os.replace`, el archivo anterior queda intacto
  y no se dejan temporales.

### 4.6 Calidad de datos

El motor `pe04_etl/quality.py` ejecuta 23 reglas sobre el resultado:

| Dimensión | Reglas |
|---|---|
| Unicidad | PK, grano `(periodo, ficha)`, ficha con un solo programa/jornada |
| Completitud | programa, campos obligatorios |
| Validez | ficha, periodo, jornada, métricas, horas, estado |
| Consistencia | activos ≤ matriculados, suma por sexo, redondeo, periodo vs nombre |
| Integridad | atomicidad 1FN, celdas compuestas residuales, espacios invisibles, cardinalidad, sumas |
| Trazabilidad | BOM/hash/sidecar, cobertura de auditoría, UUID determinista |

El tablero se genera en:

```text
reports/PE04/202605/pe04_quality_dashboard.html
reports/PE04/202605/pe04_quality_report.json
```

## 5. Decisiones formales y discrepancias con la especificación

1. **UUID v5 en lugar de v4.** La especificación pide UUID v4, pero v4 es
   aleatorio: dos ejecuciones idénticas producirían PK distintas y violarían
   `f(f(x)) = f(x)`. Se usa UUID v5 (RFC 4122) derivado de la clave natural
   `(periodo, ficha, programa, jornada)`. La PK es estable, reproducible y
   verificable. Si Power BI necesita un UUID v4 visual, puede agregarlo como
   columna calculada sin afectar la clave de integración.
2. **Fichas de 7 dígitos.** La especificación habla de 8 dígitos, pero el
   archivo real contiene fichas de 7 dígitos (p. ej. `3442618`). El dominio se
   define como entero positivo de 6 a 8 dígitos (`100000..99999999`) para no
   rechazar datos válidos del SENA.
3. **`APRENDICES_CANCELADOS` ausente.** El archivo de mayo 2026 no trae esa
   columna. Se aplica `DEFAULT 0` conforme a la especificación y se registra una
   advertencia de trazabilidad; no se inventa el dato.
4. **Auditoría separada.** `ESTADO_REGISTRO` es un atributo atómico derivado
   (`OK`/`REVISAR`). Los detalles de cada hallazgo van a
   `pe04_auditoria.csv`, una fila por regla incumplida, para no introducir
   atributos multivaluados en la tabla 1FN.
5. **CSV, XLSX y XML 2003.** El módulo acepta los tres formatos porque los
   reportes operativos de Sofía Plus aparecen en más de una exportación. El XML
   se lee como SpreadsheetML 2003 (`ss:Table`, `ss:Row`, `ss:Cell`), no como
   HTML ni como libro moderno.
6. **Duraciones en `TOTAL_HORAS`.** Un valor `20:00` no es un decimal `20.00`
   ni un porcentaje: es una duración de 20 horas. El XML lo representa como
   `1899-12-31T20:00:00.000` y el CSV como `HH:MM`. `parse_hours` normaliza
   ambas formas antes de cuantizar; `parse_decimal` se conserva para métricas
   decimales convencionales.
7. **Periodo en lote.** El periodo se infiere del preámbulo (donde el XML lo
   declara) y, si no existe, del nombre del archivo. Si dos archivos declaran
   el mismo periodo, el lote los marca `CONFLICT` y omite la partición en vez
   de sobrescribir silenciosamente. La decisión queda registrada en
   `pe04_batch_manifest.json`.
8. **Nombre del archivo curado.** El CSV normalizado se llama
   `pe04_curated_AAAA_MM.csv` (por ejemplo, `pe04_curated_2026_06.csv`) para
   que el periodo sea visible en el propio nombre, además de la carpeta de
   partición `YYYYMM`. La auditoría y los rechazos conservan nombres estables
   dentro de cada partición.

## 6. Pruebas adicionales recomendadas (opcionales)

- **Mutación**: `mutmut` o `cosmic-ray` para verificar que las pruebas detectan
  cambios deliberados en los umbrales y parsers.
- **CI**: ejecutar `pytest --cov` y `python pe04_quality_dashboard.py
  --fail-on-critical` en GitHub Actions/Azure DevOps.
- **Contrato de datos con Pandera/Great Expectations**: útil si el esquema 1FN
  se publica como contrato compartido con Power BI.
- **Prueba de propiedad sobre archivos completos**: generar lotes aleatorios de
  1.000 filas y comparar agregados con una implementación independiente.
