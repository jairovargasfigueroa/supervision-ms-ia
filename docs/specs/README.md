# Especificaciones del servicio de IA

Una carpeta por funcionalidad: `NNN-nombre/` con su `tareas.md`. El número coincide con el del plan general (`docs/plan.md` de la carpeta general).

- Los requisitos (`requisitos.md`) y el diseño (`diseno.md`) de cada funcionalidad están en `docs/specs/NNN-nombre/` de la carpeta general: todas las del servicio de IA cruzan varios proyectos.
- `tareas.md`: lista de tareas chicas con casillas `- [ ]`; se marcan al terminar cada una (con sus pruebas y su commit).

| Funcionalidad | Qué agrega aquí |
|---|---|
| 401 · Base de reportes | Proyecto, modelo de lectura `reportes_db` (proyecciones de 6 eventos), tareas programadas, carga inicial, `GET /reportes/estado` |
| 402 · Riesgo de retraso | Módulo `riesgo/` |
| 403 · Consumos atípicos | Módulo `atipicos/` |
