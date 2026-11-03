# 601 · Preparar el despliegue · tareas de `services/ia`

Requisitos y diseño: `docs/specs/601-contenedores-ci/` de la carpeta que agrupa los proyectos. Estas tareas reemplazan a las del borrador anterior (`tareas-ia.md` de la carpeta general).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» que tocan este proyecto (`docs/`, `CLAUDE.md`, `.env.example`)
- [x] Revisar que no quede ninguna dirección fija; TLS por las URL; `IA_CA_EXTRA` opcional
- [x] `Dockerfile` (Python 3.12 con uv, `modelos/` incluidos; sin `entrenamiento/` ni `ml/`) y `.dockerignore`; comandos `ia-api` (con `alembic upgrade head`), `ia-worker` e `ia-carga-inicial`; prueba de los dos procesos
- [x] `.github/workflows/ci.yml`: job `prueba` (ruff, mypy y pytest) y job `imagen` (build en PR; *push* a GHCR con `sha-<7>` y `main` en `main`)
