#!/bin/sh
# Punto de entrada de la imagen (601). Con «ia-api» aplica antes las migraciones de reportes_db (alembic upgrade head);
# cualquier otro comando (ia-worker, ia-carga-inicial, alembic …) se ejecuta tal cual. El proceso reemplaza al shell.
set -eu

if [ "${1:-}" = "ia-api" ]; then
  alembic upgrade head
fi

exec "$@"
