"""Catálogo del constructor de reportes (503): los 6 conjuntos, con sus columnas, filtros, dimensiones, medidas,
valores por defecto y ejemplos.

Solo se leen tablas y colecciones que ya existen (401, 402 y 403): `rpt_obra_resumen`, `rpt_consumo_material`,
`rpt_movimiento`, `rpt_cobro`, `rpt_planilla` y `ia_db.consumo_atipico`. Las expresiones de SQL y de MongoDB de cada
clave están en `infraestructura/` (constantes); aquí solo están las claves y sus reglas.

Dos modos: `REST` (personas, con nombres de materiales y clientes) y `MCP` (agentes, 501: filtros por id).
"""

from dataclasses import dataclass, field

# Tipos de columna (`ColumnaReporte.tipo`).
TEXTO = "TEXTO"
NUMERO = "NUMERO"
MONTO = "MONTO"
PORCENTAJE = "PORCENTAJE"
FECHA = "FECHA"
BOOLEANO = "BOOLEANO"

# Modos del catálogo.
REST = "REST"
MCP = "MCP"
AMBOS = frozenset({REST, MCP})

# Funciones de métrica.
CONTEO = "CONTEO"
SUMA = "SUMA"
PROMEDIO = "PROMEDIO"
MAXIMO = "MAXIMO"
MINIMO = "MINIMO"
GENERALES = frozenset({SUMA, PROMEDIO, MAXIMO, MINIMO})

# Clases de valor de los filtros.
V_TEXTO = "TEXTO"  # 2 a 100 caracteres (CONTIENE)
V_UUID = "UUID"
V_ID = "ID"  # id de material (ref Materiales)
V_ENUM = "ENUM"
V_PERIODO = "PERIODO"  # AAAA-MM
V_FECHA = "FECHA"  # AAAA-MM-DD
V_ENTERO = "ENTERO"
V_VERDADERO = "VERDADERO"  # solo `true`

IGUAL_EN = frozenset({"IGUAL", "EN"})
RANGO = frozenset({"DESDE", "HASTA"})
ZONAS = ("NORTE", "SUR", "ESTE", "OESTE", "CENTRO")

MESES_MAXIMOS = 24
"""Rango máximo de periodos y fechas."""


@dataclass(frozen=True)
class CampoCatalogo:
    clave: str
    titulo: str
    tipo: str
    dimension: bool = False
    """D: se puede agrupar por él."""
    funciones: frozenset[str] = frozenset()
    """M: funciones de métrica permitidas (vacío: no es medida)."""
    columna: bool = True
    """Se puede pedir en `columnas` (`monto_vencido` es solo métrica)."""


@dataclass(frozen=True)
class FiltroCatalogo:
    clave: str
    titulo: str
    operadores: frozenset[str]
    valor: str
    opciones: tuple[str, ...] = ()
    modos: frozenset[str] = AMBOS
    minimo: int = 0
    maximo: int = 0


@dataclass(frozen=True)
class ConjuntoCatalogo:
    conjunto: str
    titulo: str
    descripcion: str
    campos: tuple[CampoCatalogo, ...]
    filtros: tuple[FiltroCatalogo, ...]
    columnas_defecto: tuple[str, ...]
    orden_defecto: tuple[tuple[str, str], ...]
    ejemplos: tuple[str, ...]
    metricas_defecto: tuple[tuple[str, str | None], ...] = ((CONTEO, None),)
    """Con `agruparPor` y sin `metricas`."""
    agrupar_defecto: tuple[str, ...] = ()
    solo_agrupado: bool = False
    _por_clave: dict[str, CampoCatalogo] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_por_clave", {c.clave: c for c in self.campos})

    def campo(self, clave: str) -> CampoCatalogo | None:
        return self._por_clave.get(clave)

    def filtro(self, clave: str, modo: str) -> FiltroCatalogo | None:
        return next((f for f in self.filtros if f.clave == clave and modo in f.modos), None)

    def filtros_del_modo(self, modo: str) -> tuple[FiltroCatalogo, ...]:
        return tuple(f for f in self.filtros if modo in f.modos)

    @property
    def dimensiones(self) -> tuple[str, ...]:
        return tuple(c.clave for c in self.campos if c.dimension)

    @property
    def columnas(self) -> tuple[CampoCatalogo, ...]:
        return tuple(c for c in self.campos if c.columna)


# --- Filtros comunes ----------------------------------------------------------------------------------------------

F_OBRA = FiltroCatalogo("obra", "Obra", frozenset({"CONTIENE"}), V_TEXTO)
F_OBRA_ID = FiltroCatalogo("obraId", "Obra (id)", IGUAL_EN, V_UUID, modos=frozenset({MCP}))
F_ZONA = FiltroCatalogo("zona", "Zona", IGUAL_EN, V_ENUM, ZONAS)
F_CLIENTE = FiltroCatalogo("cliente", "Cliente", frozenset({"CONTIENE"}), V_TEXTO, modos=frozenset({REST}))
F_CLIENTE_ID = FiltroCatalogo("clienteId", "Cliente (id)", IGUAL_EN, V_UUID, modos=frozenset({MCP}))
F_PERIODO = FiltroCatalogo("periodo", "Periodo", frozenset({"IGUAL", "DESDE", "HASTA"}), V_PERIODO)
F_MATERIAL = FiltroCatalogo("material", "Material", frozenset({"CONTIENE"}), V_TEXTO, modos=frozenset({REST}))
F_MATERIAL_ID = FiltroCatalogo("materialId", "Material (id)", IGUAL_EN, V_ID)
F_FECHA = FiltroCatalogo("fecha", "Fecha", RANGO, V_FECHA)

C_OBRA = CampoCatalogo("obra", "Obra", TEXTO, dimension=True)
C_ZONA = CampoCatalogo("zona", "Zona", TEXTO, dimension=True)
C_CLIENTE = CampoCatalogo("cliente", "Cliente", TEXTO, dimension=True)
C_MATERIAL = CampoCatalogo("material", "Material", TEXTO, dimension=True)
C_PERIODO = CampoCatalogo("periodo", "Periodo", TEXTO, dimension=True)

COLUMNA_UNIDAD = ("unidad", "Unidad", TEXTO)
"""Columna que acompaña a `material` en el modo REST."""

OBRAS = ConjuntoCatalogo(
    conjunto="OBRAS",
    titulo="Obras",
    descripcion="Avance, plazo, diferencia, «En riesgo», riesgo de retraso (402) y monto de contrato de cada obra.",
    campos=(
        C_OBRA,
        C_ZONA,
        C_CLIENTE,
        CampoCatalogo("estado", "Estado", TEXTO, dimension=True),
        CampoCatalogo("nivel_riesgo", "Nivel de riesgo", TEXTO, dimension=True),
        CampoCatalogo("fecha_inicio", "Inicio", FECHA),
        CampoCatalogo("plazo_dias", "Plazo (días)", NUMERO),
        CampoCatalogo("monto_contrato", "Monto del contrato", MONTO, funciones=GENERALES),
        CampoCatalogo("avance_pct", "Avance %", PORCENTAJE, funciones=GENERALES),
        CampoCatalogo("plazo_pct", "Plazo %", PORCENTAJE, funciones=GENERALES),
        CampoCatalogo("diferencia", "Diferencia", NUMERO, funciones=GENERALES),
        CampoCatalogo("en_riesgo", "En riesgo", BOOLEANO),
        CampoCatalogo("riesgo_pct", "Riesgo de retraso %", PORCENTAJE, funciones=GENERALES),
    ),
    filtros=(
        F_OBRA,
        F_OBRA_ID,
        F_ZONA,
        F_CLIENTE,
        F_CLIENTE_ID,
        FiltroCatalogo("estado", "Estado", IGUAL_EN, V_ENUM, ("SIN_CONTRATO", "EN_EJECUCION")),
        FiltroCatalogo("nivelRiesgo", "Nivel de riesgo", IGUAL_EN, V_ENUM, ("BAJO", "MEDIO", "ALTO", "SIN_DATOS")),
        FiltroCatalogo("soloEnRiesgo", "Solo en riesgo", frozenset({"IGUAL"}), V_VERDADERO),
    ),
    columnas_defecto=("obra", "zona", "estado", "avance_pct", "plazo_pct", "diferencia", "en_riesgo"),
    orden_defecto=(("diferencia", "DESC"), ("obra", "ASC")),
    ejemplos=(
        "¿Cómo va el avance de las obras?",
        "Obras con riesgo alto de la zona norte",
        "Promedio de avance por zona",
    ),
)

CONSUMO_MATERIAL = ConjuntoCatalogo(
    conjunto="CONSUMO_MATERIAL",
    titulo="Material usado",
    descripcion=(
        "Cantidad, monto referencial (cantidad × precio referencial al registrar la salida) y número de salidas de "
        "material, por obra, material y periodo. Siempre agrupado."
    ),
    campos=(
        C_OBRA,
        C_ZONA,
        C_CLIENTE,
        C_MATERIAL,
        C_PERIODO,
        CampoCatalogo("cantidad", "Cantidad", NUMERO, funciones=GENERALES, columna=False),
        CampoCatalogo("monto", "Monto referencial", MONTO, funciones=GENERALES, columna=False),
        CampoCatalogo("salidas", "Salidas", NUMERO, funciones=GENERALES, columna=False),
    ),
    filtros=(F_OBRA, F_OBRA_ID, F_ZONA, F_CLIENTE, F_CLIENTE_ID, F_MATERIAL, F_MATERIAL_ID, F_PERIODO),
    columnas_defecto=(),
    orden_defecto=(("suma_monto", "DESC"),),
    metricas_defecto=((SUMA, "cantidad"), (SUMA, "monto"), (SUMA, "salidas")),
    agrupar_defecto=("obra", "material"),
    solo_agrupado=True,
    ejemplos=("¿Cuánto cemento se usó en Las Palmas este trimestre?", "Material usado por zona este año"),
)

MOVIMIENTOS = ConjuntoCatalogo(
    conjunto="MOVIMIENTOS",
    titulo="Movimientos de material",
    descripcion="Ingresos y salidas de material de cada obra, con la alerta «superó el stock».",
    campos=(
        C_OBRA,
        C_ZONA,
        C_CLIENTE,
        C_MATERIAL,
        CampoCatalogo("tipo", "Tipo", TEXTO, dimension=True),
        C_PERIODO,
        CampoCatalogo("supera_stock", "Superó el stock", BOOLEANO, dimension=True),
        CampoCatalogo("fecha", "Fecha", FECHA, funciones=frozenset({MAXIMO, MINIMO})),
        CampoCatalogo("cantidad", "Cantidad", NUMERO, funciones=frozenset({SUMA})),
    ),
    filtros=(
        F_OBRA,
        F_OBRA_ID,
        F_ZONA,
        F_CLIENTE,
        F_CLIENTE_ID,
        F_MATERIAL,
        F_MATERIAL_ID,
        FiltroCatalogo("tipo", "Tipo", IGUAL_EN, V_ENUM, ("INGRESO", "SALIDA")),
        FiltroCatalogo("soloSuperaStock", "Solo los que superaron el stock", frozenset({"IGUAL"}), V_VERDADERO),
        F_PERIODO,
        F_FECHA,
    ),
    columnas_defecto=("fecha", "obra", "material", "tipo", "cantidad", "supera_stock"),
    orden_defecto=(("fecha", "DESC"),),
    ejemplos=("¿Dónde salió material sin stock este mes?", "Salidas de fierro de la semana pasada"),
)

COBROS = ConjuntoCatalogo(
    conjunto="COBROS",
    titulo="Cobros",
    descripcion=(
        "Cobros emitidos al cliente: número, monto neto, emisión, vencimiento, estado y días para vencer. «Cobrado» "
        "son los cobros emitidos (no hay registro de pagos)."
    ),
    campos=(
        CampoCatalogo("numero", "Número", TEXTO),
        C_OBRA,
        C_CLIENTE,
        C_ZONA,
        CampoCatalogo("estado", "Estado", TEXTO, dimension=True),
        CampoCatalogo("mes_emision", "Mes de emisión", TEXTO, dimension=True),
        CampoCatalogo("monto", "Monto neto", MONTO, funciones=GENERALES),
        CampoCatalogo("monto_vencido", "Monto vencido", MONTO, funciones=GENERALES, columna=False),
        CampoCatalogo("emitido_en", "Emitido", FECHA),
        CampoCatalogo("vence_en", "Vence", FECHA, funciones=frozenset({MINIMO, MAXIMO})),
        CampoCatalogo("dias_para_vencer", "Días para vencer", NUMERO),
    ),
    filtros=(
        F_OBRA,
        F_OBRA_ID,
        F_ZONA,
        F_CLIENTE,
        F_CLIENTE_ID,
        FiltroCatalogo("estado", "Estado", IGUAL_EN, V_ENUM, ("PENDIENTE", "VENCIDO")),
        FiltroCatalogo(
            "venceEnDias", "Vencen en los próximos días", frozenset({"IGUAL"}), V_ENTERO, minimo=1, maximo=90
        ),
        FiltroCatalogo("periodo", "Mes de emisión", frozenset({"IGUAL", "DESDE", "HASTA"}), V_PERIODO),
    ),
    columnas_defecto=("numero", "obra", "monto", "emitido_en", "vence_en", "estado"),
    orden_defecto=(("emitido_en", "DESC"), ("numero", "DESC")),
    ejemplos=(
        "Cobros vencidos de este año",
        "¿Qué cobros vencen en los próximos 15 días?",
        "Total cobrado por obra en la zona sur",
    ),
)

PLANILLAS = ConjuntoCatalogo(
    conjunto="PLANILLAS",
    titulo="Planillas",
    descripcion=(
        "Planillas de avance aprobadas u observadas (la base de reportes no conoce las que están en borrador o "
        "presentadas sin observación): periodo, neto, avance del periodo, veces observada y último motivo."
    ),
    campos=(
        C_OBRA,
        C_ZONA,
        C_CLIENTE,
        C_PERIODO,
        CampoCatalogo("estado", "Estado", TEXTO, dimension=True),
        CampoCatalogo("neto", "Neto", MONTO, funciones=GENERALES),
        CampoCatalogo("monto_periodo", "Monto del periodo", MONTO, funciones=GENERALES),
        CampoCatalogo("avance_periodo_pct", "Avance del periodo %", PORCENTAJE, funciones=GENERALES),
        CampoCatalogo("veces_observada", "Veces observada", NUMERO, funciones=GENERALES),
        CampoCatalogo("ultimo_motivo", "Último motivo de observación", TEXTO),
        CampoCatalogo("ultimo_origen", "Observada por", TEXTO, dimension=True),
        CampoCatalogo("aprobada_en", "Aprobada", FECHA),
    ),
    filtros=(
        F_OBRA,
        F_OBRA_ID,
        F_ZONA,
        F_CLIENTE,
        F_CLIENTE_ID,
        FiltroCatalogo("estado", "Estado", IGUAL_EN, V_ENUM, ("APROBADA", "OBSERVADA")),
        F_PERIODO,
        FiltroCatalogo("aprobadaEn", "Fecha de aprobación", RANGO, V_FECHA),
    ),
    columnas_defecto=("obra", "periodo", "estado", "neto", "veces_observada", "ultimo_motivo"),
    orden_defecto=(("periodo", "DESC"),),
    ejemplos=(
        "Planillas observadas de la zona norte",
        "Neto aprobado por cliente este año",
        "Planillas aprobadas de Las Palmas desde marzo",
    ),
)

CONSUMOS_ATIPICOS = ConjuntoCatalogo(
    conjunto="CONSUMOS_ATIPICOS",
    titulo="Consumos atípicos",
    descripcion="Salidas de material marcadas como atípicas por el modelo (403), con puntaje, motivos y revisión.",
    campos=(
        C_OBRA,
        C_MATERIAL,
        C_PERIODO,
        CampoCatalogo("estado_revision", "Revisión", TEXTO, dimension=True),
        CampoCatalogo("fecha", "Fecha", FECHA),
        CampoCatalogo("cantidad", "Cantidad", NUMERO, funciones=frozenset({SUMA})),
        CampoCatalogo("puntaje", "Puntaje", NUMERO, funciones=frozenset({MAXIMO, PROMEDIO})),
        CampoCatalogo("motivos", "Motivos", TEXTO),
    ),
    filtros=(
        F_OBRA,
        F_OBRA_ID,
        F_ZONA,
        F_CLIENTE,
        F_CLIENTE_ID,
        F_MATERIAL,
        F_MATERIAL_ID,
        FiltroCatalogo("estadoRevision", "Revisión", IGUAL_EN, V_ENUM, ("PENDIENTE", "NORMAL", "CONFIRMADO")),
        F_PERIODO,
        F_FECHA,
    ),
    columnas_defecto=("fecha", "obra", "material", "cantidad", "puntaje", "motivos", "estado_revision"),
    orden_defecto=(("puntaje", "DESC"),),
    ejemplos=(
        "Consumos atípicos sin revisar de este mes",
        "¿Cuántos consumos atípicos confirmados hubo por obra?",
    ),
)

CATALOGO: dict[str, ConjuntoCatalogo] = {
    c.conjunto: c for c in (OBRAS, CONSUMO_MATERIAL, MOVIMIENTOS, COBROS, PLANILLAS, CONSUMOS_ATIPICOS)
}

TITULOS_FUNCION = {
    SUMA: "Total {titulo}",
    PROMEDIO: "Promedio {titulo}",
    MAXIMO: "Máximo {titulo}",
    MINIMO: "Mínimo {titulo}",
}
TITULOS_FUNCION_FECHA = {MAXIMO: "Última {titulo}", MINIMO: "Primera {titulo}"}


def clave_metrica(funcion: str, campo: str | None) -> str:
    """`conteo` o `<funcion en minúsculas>_<campo>` (por ejemplo `suma_monto`)."""
    return "conteo" if funcion == CONTEO else f"{funcion.lower()}_{campo}"


def titulo_metrica(conjunto: ConjuntoCatalogo, funcion: str, campo: str | None) -> str:
    if funcion == CONTEO or campo is None:
        return "Cantidad"
    origen = conjunto.campo(campo)
    titulo = origen.titulo if origen else campo
    plantillas = TITULOS_FUNCION_FECHA if origen is not None and origen.tipo == FECHA else TITULOS_FUNCION
    return plantillas.get(funcion, TITULOS_FUNCION[funcion]).format(titulo=titulo)


def tipo_metrica(conjunto: ConjuntoCatalogo, funcion: str, campo: str | None) -> str:
    if funcion == CONTEO or campo is None:
        return NUMERO
    origen = conjunto.campo(campo)
    return origen.tipo if origen else NUMERO
