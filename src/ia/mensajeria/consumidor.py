"""Consumidor de la cola `ia`: reparte por `tipo`, reintenta y manda a `plataforma.fallidos`.

Siempre confirma el original, salvo que no pueda publicar el reintento o el descarte (entonces el broker lo vuelve a
entregar al reconectar):

- aplicado, o repetido (inbox) → se confirma sin hacer nada más;
- cuerpo que no es JSON, sobre inválido, `datos` incompletos o `tipo` sin manejador → `plataforma.fallidos`;
- otro error → se vuelve a publicar en la misma cola con `x-intentos` + 1; al tercer fallo, a `plataforma.fallidos`.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ia.comun.registros import id_correlacion
from ia.mensajeria.sobre import MensajeInvalidoError, Sobre, leer_sobre

COLA = "ia"
COLA_FALLIDOS = "plataforma.fallidos"
EXCHANGE = "plataforma"
MAXIMO_FALLOS = 3

# Encabezados iguales en todos los servicios (convenciones, «Eventos y comandos»).
X_INTENTOS = "x-intentos"
X_ERROR = "x-error"
X_COLA_ORIGEN = "x-cola-origen"
X_CLAVE_ORIGEN = "x-clave-origen"

registro = logging.getLogger("ia.mensajeria")

Manejador = Callable[[Sobre], Awaitable[bool]]
"""Aplica el evento. Devuelve `True` si lo aplicó y `False` si ya estaba aplicado (inbox)."""
PasoPrevio = Callable[[Sobre], Awaitable[None]]
"""Corre antes del manejador (fuera de su transacción). Debe ser idempotente."""
PasoPosterior = Callable[[Sobre, bool], Awaitable[None]]
"""Corre después de que el manejador confirmó su transacción; recibe si el evento se aplicó ahora."""


@dataclass
class MensajeRecibido:
    cuerpo: bytes
    clave: str
    """Clave de enrutamiento con la que llegó (en un reintento, la del exchange por defecto: el nombre de la cola)."""
    encabezados: dict[str, Any] = field(default_factory=dict)
    propiedades: dict[str, Any] = field(default_factory=dict)
    """`content_type`, `message_id`, `correlation_id`, `timestamp`, `type`: se conservan al reenviar."""
    original: Any = None
    """El mensaje del broker (para confirmarlo)."""


class CanalMensajeria(Protocol):
    """Lo que el consumidor necesita del canal (en las pruebas, un doble)."""

    async def confirmar(self, mensaje: MensajeRecibido) -> None: ...

    async def publicar_en_cola(
        self, cola: str, cuerpo: bytes, encabezados: dict[str, Any], original: MensajeRecibido
    ) -> None:
        """Publica en una cola por el exchange por defecto y espera la confirmación del broker."""
        ...


@dataclass
class _Registro:
    clave: str
    manejador: Manejador
    previos: list[PasoPrevio] = field(default_factory=list)
    posteriores: list[PasoPosterior] = field(default_factory=list)


class ConsumidorMensajes:
    def __init__(self, cola: str = COLA) -> None:
        self.cola = cola
        self._por_tipo: dict[str, _Registro] = {}

    def registrar(self, clave: str, manejador: Manejador) -> None:
        """`clave` = `evento.<origen>.<Tipo>`; se reparte por el `tipo` del sobre (último segmento)."""
        tipo = clave.rsplit(".", 1)[-1]
        if tipo in self._por_tipo:
            raise ValueError(f"Ya hay un manejador para {tipo}.")
        self._por_tipo[tipo] = _Registro(clave=clave, manejador=manejador)

    def agregar_previo(self, tipo: str, paso: PasoPrevio) -> None:
        self._registro(tipo).previos.append(paso)

    def agregar_posterior(self, tipo: str, paso: PasoPosterior) -> None:
        self._registro(tipo).posteriores.append(paso)

    def claves(self) -> list[str]:
        """Claves de enrutamiento que la cola debe tener enlazadas."""
        return [r.clave for r in self._por_tipo.values()]

    def _registro(self, tipo: str) -> _Registro:
        if tipo not in self._por_tipo:
            raise ValueError(f"No hay manejador para {tipo}: regístralo antes de agregarle pasos.")
        return self._por_tipo[tipo]

    async def procesar(self, mensaje: MensajeRecibido, canal: CanalMensajeria) -> None:
        fallos_previos = _leer_intentos(mensaje.encabezados.get(X_INTENTOS))
        try:
            sobre = leer_sobre(mensaje.cuerpo)
        except MensajeInvalidoError as error:
            await self._descartar(mensaje, canal, fallos_previos, str(error))
            return

        registro_tipo = self._por_tipo.get(sobre.tipo)
        if registro_tipo is None:
            # Tipos que la cola recibe pero aún no tienen manejador: se guardan en fallidos para no perderlos.
            await self._descartar(
                mensaje, canal, fallos_previos, f"IA todavía no procesa mensajes de tipo {sobre.tipo}."
            )
            return

        marca = id_correlacion.set(sobre.correlacion_id)
        try:
            for previo in registro_tipo.previos:
                await previo(sobre)
            aplicado = await registro_tipo.manejador(sobre)
            for posterior in registro_tipo.posteriores:
                await posterior(sobre, aplicado)
            registro.info("%s id=%s %s", sobre.tipo, sobre.id, "aplicado" if aplicado else "repetido, se ignora")
        except MensajeInvalidoError as error:
            await self._descartar(mensaje, canal, fallos_previos, str(error))
            return
        except Exception as error:
            fallos = fallos_previos + 1
            texto = _texto_error(error)
            if fallos >= MAXIMO_FALLOS:
                await self._descartar(mensaje, canal, fallos, texto)
                return
            registro.warning(
                "%s id=%s falló (%s de %s), se reintenta: %s", sobre.tipo, sobre.id, fallos, MAXIMO_FALLOS, texto
            )
            encabezados = {
                **mensaje.encabezados,
                X_INTENTOS: fallos,
                X_ERROR: texto,
                X_CLAVE_ORIGEN: _clave_origen(mensaje),
            }
            await canal.publicar_en_cola(self.cola, mensaje.cuerpo, encabezados, mensaje)
        finally:
            id_correlacion.reset(marca)
        await canal.confirmar(mensaje)

    async def _descartar(self, mensaje: MensajeRecibido, canal: CanalMensajeria, fallos: int, error: str) -> None:
        """Publica el mensaje tal cual en `plataforma.fallidos`, con el error en los encabezados, y confirma."""
        registro.error("Mensaje enviado a %s (clave %s): %s", COLA_FALLIDOS, _clave_origen(mensaje), error)
        encabezados = {
            **mensaje.encabezados,
            X_INTENTOS: fallos,
            X_ERROR: error,
            X_COLA_ORIGEN: self.cola,
            X_CLAVE_ORIGEN: _clave_origen(mensaje),
        }
        await canal.publicar_en_cola(COLA_FALLIDOS, mensaje.cuerpo, encabezados, mensaje)
        await canal.confirmar(mensaje)


def _clave_origen(mensaje: MensajeRecibido) -> str:
    guardada = mensaje.encabezados.get(X_CLAVE_ORIGEN)
    if isinstance(guardada, bytes):
        guardada = guardada.decode("utf-8", "replace")
    return guardada if isinstance(guardada, str) else mensaje.clave


def _leer_intentos(valor: object) -> int:
    try:
        numero = int(valor) if valor is not None else 0  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0
    return max(numero, 0)


def _texto_error(error: BaseException) -> str:
    """Texto corto del error (sin traza ni datos del mensaje)."""
    texto = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
    return texto if len(texto) <= 500 else texto[:497] + "..."
