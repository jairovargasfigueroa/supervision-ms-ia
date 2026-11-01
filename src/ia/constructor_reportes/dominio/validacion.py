"""`validar_receta`: la receta del modelo (o de un atajo, o de un agente por MCP) contra el catálogo.

En este orden (503, «Validación de la receta»):
1. El JSON cumple `RecetaReporte` (tipos, enums, largos, rangos, sin campos de más) → si no, `IntencionNoReconocida`.
2. `conjunto = NINGUNO` → `IntencionNoReconocida`.
3. Cada clave existe en el conjunto y en el modo, cada valor tiene el tipo del filtro, periodos y fechas válidos,
   combinaciones permitidas → si no, `RecetaInvalida` con `errores` (ruta en la receta y la regla).
4. Valores por defecto del conjunto para lo que falta.

El resultado es una `RecetaValidada` (objetos del dominio, no el JSON del modelo): solo ella llega al motor.
"""

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from ia.constructor_reportes.dominio.catalogo import (
    CATALOGO,
    COLUMNA_UNIDAD,
    CONTEO,
    MCP,
    MESES_MAXIMOS,
    REST,
    V_ENTERO,
    V_ENUM,
    V_FECHA,
    V_ID,
    V_PERIODO,
    V_TEXTO,
    V_UUID,
    V_VERDADERO,
    ConjuntoCatalogo,
    FiltroCatalogo,
    clave_metrica,
    tipo_metrica,
    titulo_metrica,
)
from ia.constructor_reportes.dominio.receta import LIMITE_REST, NINGUNO, RecetaReporte

LIMITE_MCP = 50
LIMITE_MCP_DEFECTO = 20
_PERIODO = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

ValorFiltro = str | int | bool | date | tuple[str, ...]


class IntencionNoReconocida(Exception):
    """El pedido no corresponde a ningún conjunto, o la receta no tiene la forma pedida."""


@dataclass(frozen=True)
class ErrorReceta:
    campo: str
    """Ruta en la receta, por ejemplo `filtros[1].valor`."""
    mensaje: str


class RecetaInvalida(Exception):
    def __init__(self, errores: list[ErrorReceta]) -> None:
        super().__init__("; ".join(f"{e.campo}: {e.mensaje}" for e in errores))
        self.errores = errores


@dataclass(frozen=True)
class FiltroValidado:
    campo: str
    operador: str
    valor: ValorFiltro


@dataclass(frozen=True)
class MetricaValidada:
    funcion: str
    campo: str | None

    @property
    def clave(self) -> str:
        return clave_metrica(self.funcion, self.campo)


@dataclass(frozen=True)
class Columna:
    clave: str
    titulo: str
    tipo: str


@dataclass(frozen=True)
class RecetaValidada:
    conjunto: str
    modo: str
    columnas: tuple[str, ...]
    """Sin agrupación: las columnas pedidas (o las del conjunto por defecto)."""
    agrupar_por: tuple[str, ...]
    metricas: tuple[MetricaValidada, ...]
    filtros: tuple[FiltroValidado, ...]
    orden: tuple[tuple[str, str], ...]
    limite: int

    @property
    def catalogo(self) -> ConjuntoCatalogo:
        return CATALOGO[self.conjunto]

    @property
    def agrupada(self) -> bool:
        return bool(self.agrupar_por)

    def filtros_de(self, campo: str) -> list[FiltroValidado]:
        return [f for f in self.filtros if f.campo == campo]

    def claves_salida(self) -> tuple[str, ...]:
        """Claves de las columnas del resultado según el catálogo (sin la «Unidad» del modo REST)."""
        if self.agrupada:
            return self.agrupar_por + tuple(m.clave for m in self.metricas)
        return self.columnas

    def columnas_salida(self) -> list[Columna]:
        """Columnas del resultado, con «Unidad» al lado de `material` en el modo REST."""
        conjunto = self.catalogo
        salida: list[Columna] = []
        for clave in self.agrupar_por if self.agrupada else self.columnas:
            campo = conjunto.campo(clave)
            assert campo is not None
            salida.append(Columna(campo.clave, campo.titulo, campo.tipo))
            if clave == "material" and self.modo == REST:
                salida.append(Columna(*COLUMNA_UNIDAD))
        for m in self.metricas:
            salida.append(
                Columna(
                    m.clave, titulo_metrica(conjunto, m.funcion, m.campo), tipo_metrica(conjunto, m.funcion, m.campo)
                )
            )
        return salida

    def como_json(self) -> dict[str, Any]:
        """La receta aplicada (con los valores por defecto), como se guarda y se devuelve."""
        datos: dict[str, Any] = {"conjunto": self.conjunto}
        if self.columnas:
            datos["columnas"] = list(self.columnas)
        if self.filtros:
            datos["filtros"] = [
                {"campo": f.campo, "operador": f.operador, "valor": _valor_json(f.valor)} for f in self.filtros
            ]
        if self.agrupar_por:
            datos["agruparPor"] = list(self.agrupar_por)
        if self.metricas:
            datos["metricas"] = [
                {"funcion": m.funcion} if m.campo is None else {"funcion": m.funcion, "campo": m.campo}
                for m in self.metricas
            ]
        if self.orden:
            datos["orden"] = [{"por": por, "direccion": direccion} for por, direccion in self.orden]
        datos["limite"] = self.limite
        return datos


def _valor_json(valor: ValorFiltro) -> Any:
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, tuple):
        return list(valor)
    return valor


def validar_receta(datos: Any, modo: str, hoy: date) -> RecetaValidada:
    """`IntencionNoReconocida` (pasos 1 y 2) o `RecetaInvalida` (paso 3); si no, la receta con valores por defecto."""
    if not isinstance(datos, Mapping):
        raise IntencionNoReconocida("La receta no es un objeto.")
    try:
        receta = RecetaReporte.model_validate(dict(datos))
    except ValidationError as error:
        raise IntencionNoReconocida("La receta no tiene la forma pedida.") from error
    if receta.conjunto == NINGUNO:
        raise IntencionNoReconocida("El pedido no corresponde a ningún conjunto del catálogo.")
    return _Validador(CATALOGO[receta.conjunto], modo, hoy).validar(receta)


class _Validador:
    def __init__(self, conjunto: ConjuntoCatalogo, modo: str, hoy: date) -> None:
        self.c = conjunto
        self.modo = modo
        self.hoy = hoy
        self.errores: list[ErrorReceta] = []

    def error(self, campo: str, mensaje: str) -> None:
        self.errores.append(ErrorReceta(campo, mensaje))

    def validar(self, r: RecetaReporte) -> RecetaValidada:
        columnas = self._columnas(r)
        agrupar = self._agrupar(r)
        metricas = self._metricas(r, agrupar)
        filtros = self._filtros(r)
        limite = self._limite(r)
        if self.errores:
            raise RecetaInvalida(self.errores)

        # Paso 4: valores por defecto.
        if self.c.solo_agrupado and not agrupar:
            agrupar = self.c.agrupar_defecto
        if agrupar:
            columnas = ()
            if not metricas:
                metricas = tuple(MetricaValidada(f, campo) for f, campo in self.c.metricas_defecto)
        elif not columnas:
            columnas = self.c.columnas_defecto
        filtros = filtros + self._filtros_defecto(filtros)

        salida = agrupar + tuple(m.clave for m in metricas) if agrupar else columnas
        orden = self._orden(r, salida)
        if self.errores:
            raise RecetaInvalida(self.errores)
        return RecetaValidada(self.c.conjunto, self.modo, columnas, agrupar, metricas, filtros, orden, limite)

    # --- partes ---------------------------------------------------------------------------------------------------

    def _columnas(self, r: RecetaReporte) -> tuple[str, ...]:
        if r.columnas is None:
            return ()
        if r.agrupar_por:
            self.error("columnas", "Con agruparPor no van columnas: se muestran las dimensiones y las métricas.")
            return ()
        if self.c.solo_agrupado:
            self.error("agruparPor", f"El conjunto {self.c.conjunto} solo se consulta agrupado: falta agruparPor.")
            return ()
        vistas: list[str] = []
        for i, clave in enumerate(r.columnas):
            campo = self.c.campo(clave)
            if campo is None or not campo.columna:
                self.error(f"columnas[{i}]", f"«{clave}» no es una columna de {self.c.conjunto}.")
            elif clave in vistas:
                self.error(f"columnas[{i}]", f"La columna «{clave}» está repetida.")
            else:
                vistas.append(clave)
        return tuple(vistas)

    def _agrupar(self, r: RecetaReporte) -> tuple[str, ...]:
        if r.agrupar_por is None:
            return ()
        vistas: list[str] = []
        for i, clave in enumerate(r.agrupar_por):
            campo = self.c.campo(clave)
            if campo is None or not campo.dimension:
                self.error(f"agruparPor[{i}]", f"«{clave}» no es una dimensión de {self.c.conjunto}.")
            elif clave in vistas:
                self.error(f"agruparPor[{i}]", f"La dimensión «{clave}» está repetida.")
            else:
                vistas.append(clave)
        return tuple(vistas)

    def _metricas(self, r: RecetaReporte, agrupar: tuple[str, ...]) -> tuple[MetricaValidada, ...]:
        if r.metricas is None:
            return ()
        if not r.agrupar_por and not self.c.solo_agrupado:
            self.error("metricas", "Sin agruparPor no van métricas.")
            return ()
        vistas: list[MetricaValidada] = []
        for i, m in enumerate(r.metricas):
            if m.funcion == CONTEO:
                if m.campo is not None:
                    self.error(f"metricas[{i}].campo", "CONTEO no lleva campo.")
                    continue
            else:
                campo = None if m.campo is None else self.c.campo(m.campo)
                if m.campo is None:
                    self.error(f"metricas[{i}].campo", f"{m.funcion} necesita un campo.")
                    continue
                if campo is None or m.funcion not in campo.funciones:
                    self.error(f"metricas[{i}].campo", f"«{m.campo}» no admite {m.funcion} en {self.c.conjunto}.")
                    continue
            metrica = MetricaValidada(m.funcion, m.campo)
            if metrica in vistas:
                self.error(f"metricas[{i}]", "La métrica está repetida.")
                continue
            vistas.append(metrica)
        return tuple(vistas)

    def _limite(self, r: RecetaReporte) -> int:
        if self.modo == MCP:
            if r.limite is not None and r.limite > LIMITE_MCP:
                self.error("limite", f"El límite va de 1 a {LIMITE_MCP}.")
            return r.limite or LIMITE_MCP_DEFECTO
        return r.limite or LIMITE_REST

    def _orden(self, r: RecetaReporte, salida: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        if r.orden is not None:
            orden: list[tuple[str, str]] = []
            for i, o in enumerate(r.orden):
                if o.por not in salida:
                    self.error(f"orden[{i}].por", f"«{o.por}» no es una columna de la salida.")
                elif any(por == o.por for por, _ in orden):
                    self.error(f"orden[{i}].por", f"«{o.por}» está repetido en el orden.")
                else:
                    orden.append((o.por, o.direccion))
            return tuple(orden)
        por_defecto = tuple((por, d) for por, d in self.c.orden_defecto if por in salida)
        if por_defecto:
            return por_defecto
        # Sin orden del conjunto que aplique (decisión del equipo): agrupado, la primera métrica de mayor a menor;
        # sin agrupar, la primera columna de menor a mayor.
        if self.c.solo_agrupado or r.agrupar_por:
            metricas = [clave for clave in salida if self.c.campo(clave) is None]
            return ((metricas[0], "DESC"),) if metricas else ((salida[0], "ASC"),)
        return ((salida[0], "ASC"),)

    def _filtros(self, r: RecetaReporte) -> tuple[FiltroValidado, ...]:
        validados: list[tuple[int, FiltroValidado]] = []
        for i, f in enumerate(r.filtros or []):
            definicion = self.c.filtro(f.campo, self.modo)
            if definicion is None:
                otro_modo = self.c.filtro(f.campo, REST if self.modo == MCP else MCP)
                motivo = f" en el modo {self.modo}" if otro_modo else ""
                self.error(f"filtros[{i}].campo", f"«{f.campo}» no es un filtro de {self.c.conjunto}{motivo}.")
                continue
            if f.operador not in definicion.operadores:
                permitidos = ", ".join(sorted(definicion.operadores))
                self.error(f"filtros[{i}].operador", f"El filtro «{f.campo}» admite {permitidos}.")
                continue
            if any(v.campo == f.campo and v.operador == f.operador for _, v in validados):
                self.error(f"filtros[{i}]", f"El filtro «{f.campo}» con {f.operador} está repetido.")
                continue
            valor = self._valor(f"filtros[{i}].valor", definicion, f.operador, f.valor)
            if valor is not None:
                validados.append((i, FiltroValidado(f.campo, f.operador, valor)))
        self._rangos(validados)
        return tuple(v for _, v in validados)

    def _valor(self, ruta: str, d: FiltroCatalogo, operador: str, valor: Any) -> ValorFiltro | None:
        if operador == "EN":
            if not isinstance(valor, list):
                self.error(ruta, "Con EN el valor es una lista de 1 a 20 textos.")
                return None
            elementos = [self._escalar(f"{ruta}[{j}]", d, v) for j, v in enumerate(valor)]
            if any(e is None for e in elementos):
                return None
            return tuple(dict.fromkeys(str(e) for e in elementos))
        if isinstance(valor, list):
            self.error(ruta, f"Con {operador} el valor no es una lista.")
            return None
        return self._escalar(ruta, d, valor)

    def _escalar(self, ruta: str, d: FiltroCatalogo, valor: Any) -> ValorFiltro | None:
        clase = d.valor
        if clase == V_VERDADERO:
            if valor is not True:
                self.error(ruta, "El valor es true.")
                return None
            return True
        if clase == V_ENTERO:
            if isinstance(valor, bool) or not isinstance(valor, int) or not d.minimo <= valor <= d.maximo:
                self.error(ruta, f"El valor es un entero de {d.minimo} a {d.maximo}.")
                return None
            return valor
        if not isinstance(valor, str):
            self.error(ruta, "El valor es un texto.")
            return None
        if clase == V_TEXTO:
            texto = valor.strip()
            if not 2 <= len(texto) <= 100:
                self.error(ruta, "El texto tiene de 2 a 100 caracteres.")
                return None
            return texto
        if clase == V_ENUM:
            if valor not in d.opciones:
                self.error(ruta, f"El valor es uno de {', '.join(d.opciones)}.")
                return None
            return valor
        if clase == V_UUID:
            try:
                return str(uuid.UUID(valor))
            except ValueError:
                self.error(ruta, "El valor es un UUID.")
                return None
        if clase == V_ID:
            if not _ID.match(valor):
                self.error(ruta, "El valor es un id de material.")
                return None
            return valor
        if clase == V_PERIODO:
            if not _PERIODO.match(valor):
                self.error(ruta, "El periodo tiene el formato AAAA-MM.")
                return None
            if valor > _periodo(self.hoy):
                self.error(ruta, "El periodo no puede estar en el futuro.")
                return None
            return valor
        if clase == V_FECHA:
            try:
                fecha = date.fromisoformat(valor) if _FECHA.match(valor) else None
            except ValueError:
                fecha = None
            if fecha is None:
                self.error(ruta, "La fecha tiene el formato AAAA-MM-DD.")
                return None
            if fecha > self.hoy:
                self.error(ruta, "La fecha no puede estar en el futuro.")
                return None
            return fecha
        raise AssertionError(clase)  # pragma: no cover

    def _rangos(self, validados: list[tuple[int, FiltroValidado]]) -> None:
        """`DESDE ≤ HASTA` y un rango de hasta 24 meses (hasta hoy si falta `HASTA`)."""
        for campo in {v.campo for _, v in validados if v.operador in ("DESDE", "HASTA")}:
            desde = next(((i, v.valor) for i, v in validados if v.campo == campo and v.operador == "DESDE"), None)
            hasta = next(((i, v.valor) for i, v in validados if v.campo == campo and v.operador == "HASTA"), None)
            if desde is None:
                continue
            inicio = _como_fecha(desde[1])
            fin = _como_fecha(hasta[1]) if hasta else self.hoy
            ruta = f"filtros[{desde[0]}].valor"
            if hasta is not None and inicio > fin:
                self.error(ruta, "«DESDE» es posterior a «HASTA».")
            elif _sumar_meses(inicio.replace(day=1), MESES_MAXIMOS) <= fin.replace(day=1):
                self.error(ruta, f"El rango es de hasta {MESES_MAXIMOS} meses.")

    def _filtros_defecto(self, filtros: tuple[FiltroValidado, ...]) -> tuple[FiltroValidado, ...]:
        campos = {f.campo for f in filtros}
        mes = _periodo(self.hoy)
        if self.c.conjunto == "OBRAS" and "estado" not in campos:
            return (FiltroValidado("estado", "IGUAL", "EN_EJECUCION"),)
        if self.c.conjunto == "CONSUMO_MATERIAL" and "periodo" not in campos:
            return (FiltroValidado("periodo", "IGUAL", mes),)
        # Movimientos: los últimos 3 meses (incluido el actual), salvo que se filtre por periodo o por fecha
        # (decisión del equipo: un filtro de fecha reemplaza al periodo por defecto).
        if self.c.conjunto == "MOVIMIENTOS" and not campos & {"periodo", "fecha"}:
            return (FiltroValidado("periodo", "DESDE", _periodo(_sumar_meses(self.hoy.replace(day=1), -2))),)
        return ()


def _periodo(dia: date) -> str:
    return f"{dia.year:04d}-{dia.month:02d}"


def _como_fecha(valor: ValorFiltro) -> date:
    if isinstance(valor, date):
        return valor
    assert isinstance(valor, str)
    return date(int(valor[:4]), int(valor[5:7]), 1)


def _sumar_meses(dia: date, meses: int) -> date:
    total = dia.year * 12 + dia.month - 1 + meses
    return date(total // 12, total % 12 + 1, 1)


def sumar_meses(dia: date, meses: int) -> date:
    """Primer día del mes que está `meses` después (o antes, si es negativo) del mes de `dia`."""
    return _sumar_meses(dia.replace(day=1), meses)


def periodo_de(dia: date) -> str:
    """`AAAA-MM` de una fecha."""
    return _periodo(dia)
