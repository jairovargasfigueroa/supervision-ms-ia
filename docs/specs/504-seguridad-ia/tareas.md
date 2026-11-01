# 504 · Seguridad de la IA · tareas de `services/ia`

Requisitos y diseño: `docs/specs/504-seguridad-ia/` de `supervision-docs` (carpeta raíz del proyecto). Las pruebas M1 a M6 de Obras, Materiales y Finanzas se hacen en cada servicio con sus tareas de 501; las del servidor MCP de IA, aquí. Todas corren con `uv run pytest -q` (sin integración continua por ahora).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» del diseño en los documentos de este proyecto (`CLAUDE.md` y `docs/estructura.md`: reglas de IA segura)
- [x] Módulo `src/ia/seguridad/` (`delimitar.py`, `limpiar.py`, `reglas_sistema.py`) y uso obligatorio en 502 y 503
- [x] Registro de cada llamada al modelo (en el puerto `ModeloLenguaje`) sin tokens, clave de la API ni secretos
- [x] Prueba de arquitectura: en el código de la capa 5, SQL solo en `constructor_reportes/infraestructura/constructor_sql.py` y `ejecutor_sql.py` (sin `text()` armado, *f-strings* ni concatenación) y filtros de MongoDB del catálogo solo en `atipicos_mongo.py`
- [x] Pruebas S1 a S18 (reportes)
- [x] Pruebas G1 a G14 (agentes)
- [x] Pruebas M1 a M6 del servidor MCP de IA y K1 y K2 (secretos)
- [ ] Prueba manual con 10 entradas maliciosas contra el modelo real, con cada proveedor (Ollama y Anthropic), con resultados en el README
