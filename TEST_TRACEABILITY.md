# Estrategia de pruebas — ETL-PE04-NORMALIZER

Este documento responde a la pregunta: **¿cómo probar el normalizador PE-04?**
La suite implementada combina pruebas unitarias, de contrato, de integración,
basadas en propiedades y de regresión, además de un tablero de calidad que
convierte las reglas de validación en evidencia visual.

## 1. Pirámide de pruebas implementada

| Nivel | Qué verifica | Herramienta | Archivo |
|---|---|---|---|
| Unitarias | Parsing de celdas compuestas, números localizados, periodos, jornadas, limpieza de texto | `pytest` | `tests/test_parsing.py` |
| Contrato (Hoare) | Precondiciones `{P}`, postcondiciones `{Q}` e invariantes del resultado | `pytest` + `ContractViolation` | `tests/test_contracts.py` |
| Integración | XLSX/CSV reales y sintéticos, cabeceras desplazadas, BOM, Latin-1, filas en blanco | `pytest`, `openpyxl`, `pandas` | `tests/test_header_detection.py`, `tests/test_integration_real_file.py` |
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

## 3. Comandos

```powershell
# Suite completa (117 pruebas)
python -m pytest

# Solo integración contra el archivo real
python -m pytest -m integration

# Cobertura
python -m pytest --cov=pe04_etl --cov-report=term-missing

# Ejecutar el ETL
python etl_pe04_normalizer.py

# Generar el tablero de calidad
python pe04_quality_dashboard.py
```

Resultado de referencia con `PE-04_mayo 2026.xlsx`:

- 117 pruebas aprobadas.
- Cobertura de `pe04_etl`: **92 %** (líneas).
- 453 registros curados, 0 rechazados, 109 horas redondeadas de 6 a 2 decimales.
- 23 reglas de calidad aplicables, 23 aprobadas, 0 fallos críticos.

## 4. Qué cubre cada tipo de prueba

### 4.1 Unitarias

Se prueban funciones puras sin filesystem ni estado global:

- `parse_decimal`: `"116,5666667"`, `"1.234,56"`, `"(1.234,56)"`, `"N/A"`.
- `parse_integer`: `"1.234"` → `1234`, `"26.0"` → `26`, `"116,56"` → error.
- `quantize_hours`: `"1,005"` → `1.01` con `ROUND_HALF_UP`.
- `parse_composite`: mayúsculas, NBSP, guiones bajos internos y formatos inválidos.
- `parse_period`: `202605`, `2026-05`, `05/2026`, meses inválidos.
- `period_from_filename`: `PE-04_mayo 2026.xlsx` → `202605`.

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

Los fixtures generan XLSX/CSV reales con `openpyxl` y `csv`, incluyendo:

- Cabecera en filas 0, 1, 2, 3 y 4.
- Preámbulos institucionales con una firma suelta para evitar falsos positivos.
- BOM UTF-8, Latin-1/CP1252 y delimitadores `;` y `,`.
- Filas completamente vacías y columnas duplicadas.
- Archivos fantasma `~$...` y extensiones no soportadas.

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
5. **CSV y XLSX.** El módulo acepta ambos formatos; el archivo operativo actual
   es XLSX, pero la especificación menciona CSV y se soportan ambos.

## 6. Pruebas adicionales recomendadas (opcionales)

- **Mutación**: `mutmut` o `cosmic-ray` para verificar que las pruebas detectan
  cambios deliberados en los umbrales y parsers.
- **CI**: ejecutar `pytest --cov` y `python pe04_quality_dashboard.py
  --fail-on-critical` en GitHub Actions/Azure DevOps.
- **Contrato de datos con Pandera/Great Expectations**: útil si el esquema 1FN
  se publica como contrato compartido con Power BI.
- **Prueba de propiedad sobre archivos completos**: generar lotes aleatorios de
  1.000 filas y comparar agregados con una implementación independiente.
