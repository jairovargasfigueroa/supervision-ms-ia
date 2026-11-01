# Especificaciones del servicio de IA

Una carpeta por funcionalidad: `NNN-nombre/` con su `tareas.md`. El número coincide con el del plan general (`docs/plan.md` de la carpeta general).

- Los requisitos (`requisitos.md`) y el diseño (`diseno.md`) de cada funcionalidad están en `docs/specs/NNN-nombre/` de la carpeta general: todas las del servicio de IA cruzan varios proyectos.
- `tareas.md`: lista de tareas chicas con casillas `- [ ]`; se marcan al terminar cada una (con sus pruebas y su commit).

| Funcionalidad | Qué agrega aquí |
|---|---|
| 401 · Base de reportes | Proyecto, modelo de lectura `reportes_db` (proyecciones de 6 eventos), tareas programadas, carga inicial, `GET /reportes/estado` |
| 402 · Riesgo de retraso | Módulo `riesgo/`, `ml/riesgo/` (datos sintéticos y entrenamiento) y `modelos/riesgo_retraso/` |
| 403 · Consumos atípicos | Módulo `atipicos/` |
| 503 · Reportes por voz | Módulos `lenguaje/` (puerto `ModeloLenguaje`, Ollama y Anthropic) y `constructor_reportes/`; migración `0002` (lector) |
| 501 · Servidores MCP | Módulo `mcp/`: servidor MCP de IA en `/mcp` (`consultar_reporte`, `riesgo_retraso`, `consumos_atipicos`) y cliente MCP del host |
| 502 · Agentes en el ERP | Módulo `agentes/`: tres agentes (host MCP), `ejecucion_agente` y `sugerencia` en `ia_db`, endpoints de agentes y sugerencias |
| 504 · Seguridad de la IA | Módulo `seguridad/` en 502 y 503, registro de las llamadas al modelo, prueba de arquitectura y `tests/seguridad/` (pruebas con entradas maliciosas) |
| 301 · Detección de casco y chaleco | `entrenamiento/epp/`: dataset, recortes, clasificador MobileNetV3-Small con TensorFlow (entorno aparte) y exportación a TFLite para la app de obra |
