"""Canal de RabbitMQ simulado y sobres de prueba."""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from ia.mensajeria.consumidor import MensajeRecibido


@dataclass
class Publicado:
    cola: str
    cuerpo: bytes
    encabezados: dict[str, Any]


@dataclass
class CanalSimulado:
    confirmados: list[MensajeRecibido] = field(default_factory=list)
    publicados: list[Publicado] = field(default_factory=list)

    async def confirmar(self, mensaje: MensajeRecibido) -> None:
        self.confirmados.append(mensaje)

    async def publicar_en_cola(
        self, cola: str, cuerpo: bytes, encabezados: dict[str, Any], original: MensajeRecibido
    ) -> None:
        self.publicados.append(Publicado(cola, cuerpo, dict(encabezados)))

    def reentregar(self, publicado: Publicado) -> MensajeRecibido:
        """El reintento vuelve por el exchange por defecto: la clave es el nombre de la cola."""
        return MensajeRecibido(cuerpo=publicado.cuerpo, clave=publicado.cola, encabezados=publicado.encabezados)


def sobre(
    tipo: str,
    datos: dict[str, Any],
    *,
    id_: str | None = None,
    ocurrido_en: str = "2026-10-28T14:30:00Z",
    version: int = 1,
    origen: str = "obras",
) -> dict[str, Any]:
    return {
        "id": id_ or str(uuid.uuid4()),
        "tipo": tipo,
        "version": version,
        "ocurridoEn": ocurrido_en,
        "origen": origen,
        "correlacionId": str(uuid.uuid4()),
        "datos": datos,
    }


def mensaje(contenido: dict[str, Any] | bytes, clave: str) -> MensajeRecibido:
    cuerpo = contenido if isinstance(contenido, bytes) else json.dumps(contenido).encode()
    return MensajeRecibido(cuerpo=cuerpo, clave=clave, propiedades={"content_type": "application/json"})
