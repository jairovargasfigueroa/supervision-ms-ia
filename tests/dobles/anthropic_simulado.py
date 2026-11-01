"""Reglas de la Messages API de Anthropic que el transporte HTTP simulado debe imitar (las pruebas nunca llaman a la
API real)."""

import json
from typing import Any

import httpx2


def rechazo_como_la_api(pedido: httpx2.Request) -> httpx2.Response | None:
    """`400 invalid_request_error` si el pedido trae bloques `tool_use` o `tool_result` sin definir `tools`, como la
    API real («Requests which include tool_use or tool_result blocks must define tools»). `None` si es aceptable."""
    cuerpo: dict[str, Any] = json.loads(pedido.content)
    bloques = [
        b
        for m in cuerpo.get("messages", [])
        for b in (m.get("content") if isinstance(m.get("content"), list) else [])
        if isinstance(b, dict)
    ]
    if any(b.get("type") in ("tool_use", "tool_result") for b in bloques) and not cuerpo.get("tools"):
        mensaje = "Requests which include `tool_use` or `tool_result` blocks must define tools."
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": mensaje}},
        )
    return None
