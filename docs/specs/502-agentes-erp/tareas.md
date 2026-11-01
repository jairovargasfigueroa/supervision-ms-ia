# 502 · Agentes en el ERP · tareas de `services/ia`

Requisitos y diseño: `docs/specs/502-agentes-erp/` de `supervision-docs` (carpeta raíz del proyecto). Requiere 401 (procesos `ia-api` e `ia-worker`, conexión a `ia_db`, visibilidad), 402 y 403 (predicciones y consumos atípicos), 503 (puerto `ModeloLenguaje` con sus adaptadores y motor de reportes) y 501 (servidor MCP de IA y cliente MCP).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» del diseño en los documentos de este proyecto (`docs/contratos.md`, `docs/datos.md`, `docs/estructura.md`)
- [x] Variables `IA_AGENTES_CRON` e `IA_AGENTES_PROGRAMADOS_ACTIVOS` en `config.py` y `.env.example` (el modelo de lenguaje y el cliente MCP ya vienen de la 503 y la 501)
- [x] Repositorios de `ejecucion_agente` (con `proveedor_llm`, `modelo` y pasos con `servidor`) y `sugerencia` en `ia_db` con sus índices (incluido el único parcial de sugerencias pendientes)
- [ ] `src/ia/agentes/dominio/definiciones.py`: los tres agentes (misión, herramientas MCP por servidor, conjuntos permitidos en `consultar_reporte`, pasos iniciales, tipos de hallazgo, acciones con su texto)
- [ ] `src/ia/agentes/dominio/reporte.py`: modelos Pydantic del reporte, generación del JSON Schema por agente y validación adicional (obras vistas, evidencias, índices)
- [ ] Ejecutor: descubrimiento en los cuatro servidores, pasos iniciales, bucle con `ModeloLenguaje.conversar` (lista permitida, esquema de argumentos, conjunto por agente, delimitación de resultados, `isError` como paso), límites de 8 llamadas y 3 minutos, reporte con `generar_json` y un reintento
- [ ] En `ia-worker`: toma de ejecuciones desde `ejecucion_agente` (`tomada_en`, una a la vez), programación diaria sin repetir y recuperación de ejecuciones `EN_CURSO` al iniciar
- [ ] Filtros de visibilidad del residente con la utilidad de la 401 (`rpt_obra_resumen.residente_id`) y exclusión de `CONTROL_COBROS`
- [ ] Endpoints en `ia-api`: `GET /agentes`, `POST /agentes/{agente}/ejecuciones`, `GET /ejecuciones`, `GET /ejecuciones/{id}`, `GET /hallazgos`, `GET /sugerencias`, `POST /sugerencias/{id}/aprobar` y `/rechazar`, con roles y errores
- [ ] Pruebas del diseño con `ModeloSimulado` y servidores MCP de prueba (incluido el mismo guion con los dos adaptadores simulados a nivel HTTP)
- [ ] Prueba manual con el modelo real, con cada proveedor (Ollama y Anthropic), documentada en el README
