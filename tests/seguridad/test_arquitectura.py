"""Prueba de arquitectura (504, A1): en el código de la capa 5 el SQL vive solo en el constructor de reportes, sin
`text()` armado, *f-strings* ni concatenación que armen SQL, y solo `atipicos_mongo.py` arma filtros de MongoDB para el
catálogo.

Capa 5: `src/ia/agentes/`, `src/ia/constructor_reportes/`, `src/ia/lenguaje/`, `src/ia/mcp/`, `src/ia/seguridad/`.
Las proyecciones de la 401 y los repositorios de 402 y 403 quedan fuera: no reciben texto del usuario ni del modelo.
"""

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "ia"
CAPA_5 = ("agentes", "constructor_reportes", "lenguaje", "mcp", "seguridad")

# `expresiones.py` tiene las expresiones de Core (constantes) de cada columna, filtro y medida que usa
# `constructor_sql.py`: es parte del constructor (503).
CON_SQL = {
    "constructor_reportes/infraestructura/constructor_sql.py",
    "constructor_reportes/infraestructura/ejecutor_sql.py",
    "constructor_reportes/infraestructura/expresiones.py",
}
TEXTO_PERMITIDO = "SET LOCAL statement_timeout = '5s'"
# Repositorios de colecciones propias de `ia_db` (consultas guardadas, límite, ejecuciones y sugerencias) y el armado
# del worker: no reciben filtros del usuario ni del modelo. El único que arma filtros del catálogo es atipicos_mongo.
CON_MONGO = {
    "constructor_reportes/infraestructura/atipicos_mongo.py",
    "constructor_reportes/infraestructura/consultas_mongo.py",
    "constructor_reportes/infraestructura/limite_mongo.py",
    "agentes/infraestructura/repositorio_mongo.py",
    "agentes/infraestructura/worker.py",
}
FILTROS_DEL_CATALOGO = {"constructor_reportes/infraestructura/atipicos_mongo.py"}

MODULOS_SQL = ("sqlalchemy", "psycopg", "asyncpg", "sqlite3")
MODULOS_MONGO = ("pymongo", "bson", "motor")
PROHIBIDAS = {"literal_column", "exec_driver_sql", "TextClause", "from_statement"}  # literal() es un parámetro enlazado
PALABRAS_SQL = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|FROM|WHERE|JOIN|GRANT|ALTER|CREATE|UNION|TRUNCATE)\b", re.IGNORECASE
)


def archivos_capa_5() -> list[tuple[str, str]]:
    salida = []
    for carpeta in CAPA_5:
        for ruta in sorted((SRC / carpeta).rglob("*.py")):
            salida.append((ruta.relative_to(SRC).as_posix(), ruta.read_text(encoding="utf-8")))
    return salida


def modulos_importados(arbol: ast.AST) -> set[str]:
    modulos = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            modulos |= {a.name for a in nodo.names}
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            modulos.add(nodo.module)
    return modulos


def usa(modulos: set[str], raices: tuple[str, ...]) -> bool:
    return any(m == r or m.startswith(r + ".") for m in modulos for r in raices)


def problemas_sql(fuente: str) -> list[str]:
    """`text()` solo con el texto constante permitido; sin llamadas que acepten SQL crudo; sin *f-strings*,
    concatenación ni formato de cadenas que contengan SQL."""
    problemas = []
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Call):
            nombre = nodo.func.id if isinstance(nodo.func, ast.Name) else getattr(nodo.func, "attr", "")
            if nombre == "text":
                argumento = nodo.args[0] if len(nodo.args) == 1 and not nodo.keywords else None
                if not (isinstance(argumento, ast.Constant) and argumento.value == TEXTO_PERMITIDO):
                    problemas.append(f"línea {nodo.lineno}: text() con algo distinto del statement_timeout constante")
            if nombre in PROHIBIDAS:
                problemas.append(f"línea {nodo.lineno}: {nombre}() acepta SQL crudo")
            if nombre == "format" and isinstance(nodo.func, ast.Attribute):
                base = nodo.func.value
                if isinstance(base, ast.Constant) and isinstance(base.value, str) and PALABRAS_SQL.search(base.value):
                    problemas.append(f"línea {nodo.lineno}: .format() arma SQL")
        elif isinstance(nodo, ast.JoinedStr):
            constante = "".join(
                v.value for v in nodo.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if PALABRAS_SQL.search(constante):
                problemas.append(f"línea {nodo.lineno}: f-string que arma SQL")
        elif isinstance(nodo, ast.BinOp) and isinstance(nodo.op, ast.Add | ast.Mod):
            for lado in (nodo.left, nodo.right):
                if isinstance(lado, ast.Constant) and isinstance(lado.value, str) and PALABRAS_SQL.search(lado.value):
                    problemas.append(f"línea {nodo.lineno}: concatenación o formato que arma SQL")
    return problemas


def test_solo_el_constructor_de_reportes_toca_sql() -> None:
    con_sql = {ruta for ruta, fuente in archivos_capa_5() if usa(modulos_importados(ast.parse(fuente)), MODULOS_SQL)}
    assert con_sql == CON_SQL


def test_sin_text_armado_ni_cadenas_que_armen_sql() -> None:
    problemas = {ruta: problemas_sql(fuente) for ruta, fuente in archivos_capa_5() if ruta in CON_SQL}
    assert problemas == {ruta: [] for ruta in CON_SQL}


def test_ningun_otro_archivo_de_la_capa_5_arma_sql_en_cadenas() -> None:
    for ruta, fuente in archivos_capa_5():
        problemas = [p for p in problemas_sql(fuente) if "text()" not in p]
        assert problemas == [], ruta


def test_mongodb_solo_en_sus_repositorios_y_los_filtros_del_catalogo_solo_en_atipicos_mongo() -> None:
    con_mongo = set()
    del_catalogo = set()
    for ruta, fuente in archivos_capa_5():
        modulos = modulos_importados(ast.parse(fuente))
        if usa(modulos, MODULOS_MONGO) or "AsyncDatabase" in fuente:
            con_mongo.add(ruta)
            if any(m.startswith("ia.constructor_reportes.dominio") for m in modulos) or "ConsultaResuelta" in fuente:
                del_catalogo.add(ruta)
    assert con_mongo == CON_MONGO
    assert del_catalogo == FILTROS_DEL_CATALOGO


def test_el_modelo_no_llega_al_sql_ni_a_mongodb() -> None:
    """Ni `lenguaje/` ni `mcp/` ni `seguridad/` ni la lógica de los agentes importan bases de datos."""
    for ruta, fuente in archivos_capa_5():
        if ruta.startswith(("lenguaje/", "mcp/", "seguridad/", "agentes/dominio/", "agentes/aplicacion/")):
            assert not usa(modulos_importados(ast.parse(fuente)), MODULOS_SQL + MODULOS_MONGO), ruta


@pytest.mark.parametrize(
    "fuente",
    [
        'from sqlalchemy import text\nq = text(f"SELECT * FROM {tabla}")',
        "from sqlalchemy import text\nq = text('SELECT 1')",
        'consulta = "SELECT * FROM rpt_cobro WHERE id = " + valor',
        'consulta = f"DELETE FROM rpt_obra_resumen WHERE nombre = {nombre}"',
        'consulta = "SELECT {} FROM x".format(columna)',
        'consulta = "UPDATE x SET y = %s" % valor',
        "from sqlalchemy import literal_column\nc = literal_column(orden)",
    ],
)
def test_la_regla_detecta_sql_armado(fuente: str) -> None:
    assert problemas_sql(fuente)


def test_la_regla_acepta_el_patron_de_ilike_y_el_tiempo_maximo() -> None:
    fuente = f'from sqlalchemy import text\nT = text("{TEXTO_PERMITIDO}")\npatron = f"%{{escapado}}%"'
    assert problemas_sql(fuente) == []
