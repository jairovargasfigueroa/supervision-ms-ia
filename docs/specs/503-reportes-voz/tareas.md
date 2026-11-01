# 503 · Reportes generativos por voz · tareas de `services/ia`

Requisitos y diseño: `docs/specs/503-reportes-voz/` de `supervision-docs` (carpeta raíz del proyecto). Requiere la base de reportes de la 401 (tablas `rpt_*` y conexión SQLAlchemy), la 402 (columnas `riesgo_retraso` y `nivel_riesgo`) y la 403 (`ia_db.consumo_atipico`). Esta spec **crea** el puerto `ModeloLenguaje` (lo reutiliza la 502).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» del diseño en los documentos de este proyecto (`docs/contratos.md`, `docs/datos.md`, `docs/estructura.md`, `CLAUDE.md`)
- [ ] Agregar `ollama` 0.6 y `anthropic` 2; variables `IA_LLM_PROVEEDOR`, `IA_LLM_MODELO`, `IA_OLLAMA_URL`, `IA_ANTHROPIC_API_KEY` e `IA_REPORTES_DB_URL_LECTURA` en `config.py` y `.env.example` (la clave vacía; el servicio no arranca con un proveedor desconocido o con `anthropic` sin clave)
- [ ] Módulo `src/ia/lenguaje/`: puerto `ModeloLenguaje` (`conversar` y `generar_json`), errores, `adaptar_esquema` por proveedor, adaptadores `ModeloOllama` y `ModeloAnthropic`, `crear_modelo_lenguaje`; `ModeloSimulado` en `tests/dobles/`; pruebas de los adaptadores con transporte HTTP simulado
- [ ] Migración de Alembic con los `GRANT SELECT` de `ia_reportes_lector` sobre `rpt_obra_resumen`, `rpt_consumo_material`, `rpt_movimiento`, `rpt_cobro` y `rpt_planilla`
- [ ] `constructor_reportes/dominio/`: catálogo de los 6 conjuntos (modos `REST` y `MCP`), `RecetaReporte`, `validar_receta` (en orden, con `errores[]`), atajos y resultado (plantilla del resumen)
- [ ] Motor: `expresiones.py` y `constructor_sql.py` (SQLAlchemy Core, solo valores enlazados, escape de `ILIKE`, `limite + 1`), `ejecutor_sql.py` (lector, `READ ONLY`, 5 s) y `atipicos_mongo.py` (filtro y agregación con constantes, 5 s); conversión de filas a texto
- [ ] Nombres: catálogo de Materiales con el token del usuario (caché de 5 min) y directorio de clientes del cuerpo; avisos y `503 SERVICIO_NO_DISPONIBLE` cuando falta lo necesario para un filtro
- [ ] `InterpretarPedido` (catálogo compacto, `<pregunta_usuario>`, 20 s) y resumen (datos con `como_dato`, 25 s, plantilla de respaldo)
- [ ] Repositorios de `consulta_reporte` y `limite_consulta` (TTL y `$inc` atómico) en `ia_db`
- [ ] Endpoints `POST /consultas-reporte`, `GET /consultas-reporte`, `GET /catalogo-reportes` (solo `admin` y `gerente`) con sus errores
- [ ] Pruebas del diseño con `ModeloSimulado`, PostgreSQL con las migraciones de la 401 y MongoDB reales (incluidos los permisos de `ia_reportes_lector`)
- [ ] Prueba manual con cada proveedor (Ollama y Anthropic): 20 preguntas de ejemplo y los 10 atajos, con resultados anotados en el README
