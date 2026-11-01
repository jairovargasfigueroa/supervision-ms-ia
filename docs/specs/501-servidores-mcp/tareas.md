# 501 · Servidores MCP · tareas de `services/ia`

Requisitos y diseño: `docs/specs/501-servidores-mcp/` de `supervision-docs` (carpeta raíz del proyecto). Requiere el servicio `services/ia` de la 401 (estructura `src/ia/`, `config.py`, validación del token), los repositorios de la 402 y la 403 y el motor de reportes de la 503 (`validar_receta` y `MotorReportes`).

El servicio de IA es aquí **host** (cliente MCP de los agentes) y **servidor** (`consultar_reporte`, `riesgo_retraso`, `consumos_atipicos`).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» del diseño en los documentos de este proyecto (`docs/contratos.md`, `docs/estructura.md`, `docs/servicio.md`)
- [x] Agregar el paquete `mcp` 2 (2.3.x) y las variables `IA_AGENTES_CLIENT_ID`, `IA_AGENTES_CLIENT_SECRET`, `IA_OBRAS_MCP_URL`, `IA_MATERIALES_MCP_URL`, `IA_FINANZAS_MCP_URL`, `IA_PROPIO_MCP_URL` e `IA_MCP_HOSTS_PERMITIDOS` en `config.py` (con `.env.example`, el secreto vacío)
- [x] Servidor: `src/ia/mcp/servidor/servidor.py` (`MCPServer` montado en `/mcp` de `ia-api`, sin estado, respuestas JSON, `session_manager.run()` en el *lifespan*, hosts permitidos) y `seguridad.py` (solo `agente_ia`; las rutas REST siguen rechazándolo)
- [x] Herramientas `riesgo_retraso` y `consumos_atipicos` (repositorios de la 402 y la 403, solo lectura, `truncado`, anotación de solo lectura)
- [x] Herramienta `consultar_reporte` (receta modo `MCP`, límite 50, errores `ARGUMENTO_INVALIDO` con rutas y `ERROR_INTERNO`)
- [x] Pruebas del servidor: 401 / 403 / 200, `tools/list` exacto, cifras iguales al REST, argumentos maliciosos, solo lectura (ninguna tabla ni colección cambia)
- [x] Cliente: `src/ia/mcp/cliente/token.py` (*client credentials* con caché hasta 30 s antes del vencimiento; pruebas con un Keycloak simulado)
- [x] `src/ia/mcp/cliente/cliente.py`: `listar_herramientas()` y `llamar()` con `Authorization`, `X-Request-Id` y tiempo máximo de 10 s
- [ ] `src/ia/mcp/cliente/catalogo.py`: mapa herramienta → servidor (cuatro servidores) y comparación de esquemas con `tools/list` (herramientas desconocidas se ignoran con advertencia)
- [ ] Pruebas del cliente contra un servidor MCP de prueba hecho con el mismo SDK (camino feliz, servidor caído, servidor lento, herramienta desconocida)
