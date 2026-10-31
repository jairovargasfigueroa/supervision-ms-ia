"""Conexión a RabbitMQ con aio-pika: topología de la cola `ia`, prefetch 10, `ack` manual y reconexión."""

import asyncio
import logging
from contextlib import suppress
from typing import Any

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractIncomingMessage,
    AbstractQueue,
    AbstractRobustConnection,
)

from ia.mensajeria.consumidor import COLA_FALLIDOS, EXCHANGE, ConsumidorMensajes, MensajeRecibido

PREFETCH = 10
ESPERA_RECONEXION_S = 5.0

registro = logging.getLogger("ia.mensajeria.rabbit")


async def preparar_topologia(canal: AbstractChannel, cola: str, claves: list[str]) -> AbstractQueue:
    """Idempotente: con los mismos argumentos que las definiciones de `infra` no cambia nada; si falta algo, lo crea."""
    exchange = await canal.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    await canal.declare_queue(COLA_FALLIDOS, durable=True)
    queue = await canal.declare_queue(cola, durable=True)
    for clave in claves:
        await queue.bind(exchange, routing_key=clave)
    return queue


class CanalAioPika:
    """Adaptador de `CanalMensajeria` sobre un canal de aio-pika con confirmaciones del broker."""

    def __init__(self, canal: AbstractChannel) -> None:
        self._canal = canal

    async def confirmar(self, mensaje: MensajeRecibido) -> None:
        original: AbstractIncomingMessage = mensaje.original
        await original.ack()

    async def publicar_en_cola(
        self, cola: str, cuerpo: bytes, encabezados: dict[str, Any], original: MensajeRecibido
    ) -> None:
        propiedades = original.propiedades
        await self._canal.default_exchange.publish(
            aio_pika.Message(
                body=cuerpo,
                headers=encabezados,
                content_type=propiedades.get("content_type") or "application/json",
                message_id=propiedades.get("message_id"),
                correlation_id=propiedades.get("correlation_id"),
                timestamp=propiedades.get("timestamp"),
                type=propiedades.get("type"),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=cola,
        )


def traducir(mensaje: AbstractIncomingMessage) -> MensajeRecibido:
    return MensajeRecibido(
        cuerpo=mensaje.body,
        clave=mensaje.routing_key or "",
        encabezados=dict(mensaje.headers or {}),
        propiedades={
            "content_type": mensaje.content_type,
            "message_id": mensaje.message_id,
            "correlation_id": mensaje.correlation_id,
            "timestamp": mensaje.timestamp,
            "type": mensaje.type,
        },
        original=mensaje,
    )


class ConexionRabbit:
    """Consume la cola del consumidor. `connect_robust` reconecta y vuelve a declarar la cola y el consumo solo."""

    def __init__(self, url: str, consumidor: ConsumidorMensajes, prefetch: int = PREFETCH) -> None:
        self._url = url
        self._consumidor = consumidor
        self._prefetch = prefetch
        self._conexion: AbstractRobustConnection | None = None

    async def iniciar(self, detener: asyncio.Event) -> bool:
        """Se conecta (reintenta cada 5 s hasta lograrlo o hasta que se pida detener) y empieza a consumir."""
        while not detener.is_set():
            try:
                self._conexion = await aio_pika.connect_robust(self._url)
                break
            except Exception as error:
                registro.warning(
                    "No se pudo conectar a RabbitMQ (%s); se reintenta en %s s.", error, ESPERA_RECONEXION_S
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(detener.wait(), ESPERA_RECONEXION_S)
        if self._conexion is None:
            return False
        canal = await self._conexion.channel(publisher_confirms=True)
        await canal.set_qos(prefetch_count=self._prefetch)
        queue = await preparar_topologia(canal, self._consumidor.cola, self._consumidor.claves())
        adaptador = CanalAioPika(canal)

        async def al_recibir(mensaje: AbstractIncomingMessage) -> None:
            try:
                await self._consumidor.procesar(traducir(mensaje), adaptador)
            except Exception:
                # No se confirmó: RabbitMQ lo vuelve a entregar al cerrarse el canal.
                registro.exception("No se pudo procesar ni reintentar un mensaje.")

        await queue.consume(al_recibir, no_ack=False)
        registro.info("Conectado; consumiendo la cola %s.", self._consumidor.cola)
        return True

    async def cerrar(self) -> None:
        if self._conexion is not None:
            await self._conexion.close()
            self._conexion = None
