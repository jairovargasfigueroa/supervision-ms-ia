"""Permisos del usuario de solo lectura `ia_reportes_lector` (503: constructor de reportes).

El rol (`LOGIN`, `default_transaction_read_only = on`) lo crea el script de inicio de PostgreSQL de `infra`; aquí
solo se le da `SELECT` sobre las cinco tablas que usa el catálogo (no sobre `evento_aplicado` ni `carga_inicial`).
Si el rol no existe, la migración falla con un mensaje claro (hay que crearlo antes).

Revisión: 0002
Anterior: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROL = "ia_reportes_lector"
TABLAS = ("rpt_obra_resumen", "rpt_consumo_material", "rpt_movimiento", "rpt_cobro", "rpt_planilla")


def upgrade() -> None:
    existe = op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :rol"), {"rol": ROL}).scalar()
    if not existe:
        raise RuntimeError(
            f"Falta el rol {ROL} en PostgreSQL: créalo antes de migrar (script de inicio de PostgreSQL de infra, 503)."
        )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ROL}")
    for tabla in TABLAS:
        op.execute(f"GRANT SELECT ON {tabla} TO {ROL}")


def downgrade() -> None:
    for tabla in TABLAS:
        op.execute(f"REVOKE SELECT ON {tabla} FROM {ROL}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROL}")
