"""Cobro pagado (201): `rpt_cobro.pagado_en`.

`rpt_cobro.estado` es `varchar` sin restricción: admite `PAGADO` sin cambios. El `GRANT SELECT` de
`ia_reportes_lector` (0002) es sobre la tabla y alcanza a la columna nueva.

Revisión: 0003
Anterior: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rpt_cobro", sa.Column("pagado_en", sa.Date, nullable=True))


def downgrade() -> None:
    op.drop_column("rpt_cobro", "pagado_en")
