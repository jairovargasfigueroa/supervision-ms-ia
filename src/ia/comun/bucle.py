"""Arranque del bucle de asyncio de los tres comandos."""

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def ejecutar_async[T](corrutina: Coroutine[Any, Any, T]) -> T:
    """`asyncio.run` con el bucle *selector* en Windows: psycopg async no funciona con el *proactor*."""
    if sys.platform == "win32":
        return asyncio.run(corrutina, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(corrutina)
