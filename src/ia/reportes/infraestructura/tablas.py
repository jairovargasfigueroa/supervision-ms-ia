"""Tablas de `reportes_db` (SQLAlchemy Core). Las crea la migración `0001_inicial`; aquí solo se describen."""

from sqlalchemy import CHAR, Boolean, Column, Date, DateTime, Index, Integer, Numeric, String, Table, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from ia.comun.base_datos import metadata

rpt_obra_resumen = Table(
    "rpt_obra_resumen",
    metadata,
    Column("obra_id", UUID(as_uuid=False), primary_key=True),
    Column("nombre", String, nullable=False),
    Column("ubicacion", String),
    Column("zona", String),
    Column("cliente_id", UUID(as_uuid=False)),
    Column("residente_id", UUID(as_uuid=False)),
    Column("maestro_id", UUID(as_uuid=False)),
    Column("almacenero_id", UUID(as_uuid=False)),
    Column("estado", String, nullable=False),
    Column("fecha_inicio", Date),
    Column("plazo_dias", Integer),
    Column("monto_contrato", Numeric(14, 2)),
    Column("avance_pct", Numeric(5, 2), nullable=False, server_default=text("0")),
    Column("plazo_pct", Numeric(5, 2), nullable=False, server_default=text("0")),
    Column("riesgo_retraso", Numeric(4, 3)),
    Column("nivel_riesgo", String),
    Column("actualizado_en", DateTime(timezone=True), nullable=False),
    Index("ix_rpt_obra_resumen_residente_id", "residente_id"),
)

rpt_planilla = Table(
    "rpt_planilla",
    metadata,
    Column("planilla_id", UUID(as_uuid=False), primary_key=True),
    Column("obra_id", UUID(as_uuid=False), nullable=False),
    Column("periodo", CHAR(7)),
    Column("estado", String, nullable=False),
    Column("neto", Numeric(14, 2)),
    Column("monto_periodo", Numeric(14, 2)),
    Column("avance_periodo_pct", Numeric(5, 2)),
    Column("veces_observada", Integer, nullable=False, server_default=text("0")),
    Column("ultimo_motivo", Text),
    Column("ultimo_origen", String),
    Column("aprobada_en", DateTime(timezone=True)),
    Column("cargada_en", DateTime(timezone=True)),
    Column("actualizado_en", DateTime(timezone=True), nullable=False),
    Index("ix_rpt_planilla_obra_id", "obra_id"),
)

rpt_consumo_material = Table(
    "rpt_consumo_material",
    metadata,
    Column("obra_id", UUID(as_uuid=False), primary_key=True),
    Column("periodo", CHAR(7), primary_key=True),
    Column("material_id", String, primary_key=True),
    Column("partida_id", UUID(as_uuid=False), primary_key=True),
    Column("cantidad", Numeric(14, 3), nullable=False, server_default=text("0")),
    Column("monto", Numeric(14, 2), nullable=False, server_default=text("0")),
    Column("salidas", Integer, nullable=False, server_default=text("0")),
    Column("actualizado_en", DateTime(timezone=True), nullable=False),
)

rpt_movimiento = Table(
    "rpt_movimiento",
    metadata,
    Column("movimiento_id", UUID(as_uuid=False), primary_key=True),
    Column("obra_id", UUID(as_uuid=False), nullable=False),
    Column("material_id", String, nullable=False),
    Column("partida_id", UUID(as_uuid=False)),
    Column("tipo", String, nullable=False),
    Column("cantidad", Numeric(14, 3), nullable=False),
    Column("precio_referencial", Numeric(14, 2)),
    Column("periodo", CHAR(7), nullable=False),
    Column("supera_stock", Boolean, nullable=False, server_default=text("false")),
    Column("registrado_en", DateTime(timezone=True), nullable=False),
    Index("ix_rpt_movimiento_obra_material_registrado", "obra_id", "material_id", "registrado_en"),
    Index("ix_rpt_movimiento_material_tipo", "material_id", "tipo"),
)

rpt_cobro = Table(
    "rpt_cobro",
    metadata,
    Column("cobro_id", UUID(as_uuid=False), primary_key=True),
    Column("numero", String),
    Column("obra_id", UUID(as_uuid=False)),
    Column("planilla_id", UUID(as_uuid=False)),
    Column("cliente_id", UUID(as_uuid=False)),
    Column("zona", String),
    Column("monto", Numeric(14, 2)),
    Column("emitido_en", Date),
    Column("vence_en", Date),
    Column("estado", String),
    Column("actualizado_en", DateTime(timezone=True), nullable=False),
)

carga_inicial = Table(
    "carga_inicial",
    metadata,
    Column("carga_id", UUID(as_uuid=False), primary_key=True),
    Column("iniciada_en", DateTime(timezone=True), nullable=False),
    Column("terminada_en", DateTime(timezone=True)),
    Column("estado", String, nullable=False),
    Column("obras", Integer, nullable=False, server_default=text("0")),
    Column("filas", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error", Text),
)
