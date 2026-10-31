"""Tablas iniciales de reportes_db (401): rpt_*, evento_aplicado y carga_inicial.

Revisión: 0001
Anterior: ninguna
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=False)
TIMESTAMPTZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "rpt_obra_resumen",
        sa.Column("obra_id", UUID, primary_key=True),
        sa.Column("nombre", sa.String, nullable=False),
        sa.Column("ubicacion", sa.String),
        sa.Column("zona", sa.String),
        sa.Column("cliente_id", UUID),
        sa.Column("residente_id", UUID),
        sa.Column("maestro_id", UUID),
        sa.Column("almacenero_id", UUID),
        sa.Column("estado", sa.String, nullable=False),
        sa.Column("fecha_inicio", sa.Date),
        sa.Column("plazo_dias", sa.Integer),
        sa.Column("monto_contrato", sa.Numeric(14, 2)),
        sa.Column("avance_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("plazo_pct", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("riesgo_retraso", sa.Numeric(4, 3)),
        sa.Column("nivel_riesgo", sa.String),
        sa.Column("actualizado_en", TIMESTAMPTZ, nullable=False),
    )
    op.create_index("ix_rpt_obra_resumen_residente_id", "rpt_obra_resumen", ["residente_id"])

    op.create_table(
        "rpt_planilla",
        sa.Column("planilla_id", UUID, primary_key=True),
        sa.Column("obra_id", UUID, nullable=False),
        sa.Column("periodo", sa.CHAR(7)),
        sa.Column("estado", sa.String, nullable=False),
        sa.Column("neto", sa.Numeric(14, 2)),
        sa.Column("monto_periodo", sa.Numeric(14, 2)),
        sa.Column("avance_periodo_pct", sa.Numeric(5, 2)),
        sa.Column("veces_observada", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("ultimo_motivo", sa.Text),
        sa.Column("ultimo_origen", sa.String),
        sa.Column("aprobada_en", TIMESTAMPTZ),
        sa.Column("cargada_en", TIMESTAMPTZ),
        sa.Column("actualizado_en", TIMESTAMPTZ, nullable=False),
    )
    op.create_index("ix_rpt_planilla_obra_id", "rpt_planilla", ["obra_id"])

    op.create_table(
        "rpt_consumo_material",
        sa.Column("obra_id", UUID, primary_key=True),
        sa.Column("periodo", sa.CHAR(7), primary_key=True),
        sa.Column("material_id", sa.String, primary_key=True),
        sa.Column("partida_id", UUID, primary_key=True),
        sa.Column("cantidad", sa.Numeric(14, 3), nullable=False, server_default=sa.text("0")),
        sa.Column("monto", sa.Numeric(14, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("salidas", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("actualizado_en", TIMESTAMPTZ, nullable=False),
    )

    op.create_table(
        "rpt_movimiento",
        sa.Column("movimiento_id", UUID, primary_key=True),
        sa.Column("obra_id", UUID, nullable=False),
        sa.Column("material_id", sa.String, nullable=False),
        sa.Column("partida_id", UUID),
        sa.Column("tipo", sa.String, nullable=False),
        sa.Column("cantidad", sa.Numeric(14, 3), nullable=False),
        sa.Column("precio_referencial", sa.Numeric(14, 2)),
        sa.Column("periodo", sa.CHAR(7), nullable=False),
        sa.Column("supera_stock", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("registrado_en", TIMESTAMPTZ, nullable=False),
    )
    op.create_index(
        "ix_rpt_movimiento_obra_material_registrado", "rpt_movimiento", ["obra_id", "material_id", "registrado_en"]
    )
    op.create_index("ix_rpt_movimiento_material_tipo", "rpt_movimiento", ["material_id", "tipo"])

    op.create_table(
        "rpt_cobro",
        sa.Column("cobro_id", UUID, primary_key=True),
        sa.Column("numero", sa.String),
        sa.Column("obra_id", UUID),
        sa.Column("planilla_id", UUID),
        sa.Column("cliente_id", UUID),
        sa.Column("zona", sa.String),
        sa.Column("monto", sa.Numeric(14, 2)),
        sa.Column("emitido_en", sa.Date),
        sa.Column("vence_en", sa.Date),
        sa.Column("estado", sa.String),
        sa.Column("actualizado_en", TIMESTAMPTZ, nullable=False),
    )

    op.create_table(
        "evento_aplicado",
        sa.Column("evento_id", UUID, primary_key=True),
        sa.Column("tipo", sa.String, nullable=False),
        sa.Column("aplicado_en", TIMESTAMPTZ, nullable=False),
    )
    op.create_index("ix_evento_aplicado_tipo_aplicado_en", "evento_aplicado", ["tipo", "aplicado_en"])

    op.create_table(
        "carga_inicial",
        sa.Column("carga_id", UUID, primary_key=True),
        sa.Column("iniciada_en", TIMESTAMPTZ, nullable=False),
        sa.Column("terminada_en", TIMESTAMPTZ),
        sa.Column("estado", sa.String, nullable=False),
        sa.Column("obras", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("filas", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text),
    )


def downgrade() -> None:
    for tabla in (
        "carga_inicial",
        "evento_aplicado",
        "rpt_cobro",
        "rpt_movimiento",
        "rpt_consumo_material",
        "rpt_planilla",
        "rpt_obra_resumen",
    ):
        op.drop_table(tabla)
