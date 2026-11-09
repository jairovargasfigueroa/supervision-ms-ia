# 505 · Asistente de reportes y agentes completos · tareas de `IA`

Requisitos y diseño: `docs/specs/505-asistente-reportes/` de la carpeta que agrupa los proyectos (aprobada el 09/11/2026).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» de IA: `docs/contratos.md` (endpoint con conversación, filtros nuevos del catálogo) y `docs/datos.md` (`conversacion_reporte`, `receta_cache`, `consulta_reporte.conversacion_id`)
- [x] Modelo de lenguaje: `LlmNoDisponible.reintentable` en los dos adaptadores, `ModeloConReintentos` (2 reintentos, 1 y 3 s) y caché del sistema con `cache_control` en Anthropic
- [x] Catálogo ampliado: `EN` en `obra`, `cliente` y `material`; filtros numéricos y de fecha; `nivelRiesgo` y `soloEnRiesgo` en los demás conjuntos; campos cruzados de Obras (planillas, cobros, salidas sin stock) en expresiones y SQL
- [x] Interpretar con `RespuestaInterprete` (RECETA, ACLARAR, NINGUNO), instrucciones nuevas y ejemplos validados por prueba; autocorrección hasta 3 intentos
- [x] Conversación: `conversacion_reporte` (TTL 30 min, dueño, 20 turnos), dudas del código (varios, ninguno con parecidos, «Todos», opción elegida), tope de 5 preguntas, seguimientos sobre la última receta, caché de recetas (24 h) y límite de 20 por minuto
- [x] `POST /consultas-reporte`: pedido con `conversacionId` (texto desde 1 carácter con conversación), respuesta `{conversacionId, tipo, consulta, aclaracion}` y `404 CONVERSACION_NO_ENCONTRADA`
- [x] Agentes: límites nuevos, reintento MCP, reporte con 3 intentos, `$periodo_actual`, paso compuesto `consumo_vs_previsto` y catálogo de contrato con `consumos_partida.obraId` opcional
- [x] Pruebas completas (`pytest`, incluidas las de seguridad), `ruff` y `mypy`
