"""Errores de los casos de uso de consumos atípicos (la capa web los traduce a Problem Details)."""


class ErrorAtipicos(Exception):
    def __init__(self, mensaje: str) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje


class DatosInvalidos(ErrorAtipicos):
    """400 DATOS_INVALIDOS: estado o fechas que no se pueden usar."""


class DemasiadasObras(ErrorAtipicos):
    """400 DEMASIADAS_OBRAS: más de 100 obras en el resumen."""


class ObraNoEncontrada(ErrorAtipicos):
    """404 OBRA_NO_ENCONTRADA: la obra no existe o el usuario no la ve."""


class ConsumoNoEncontrado(ErrorAtipicos):
    """404 CONSUMO_ATIPICO_NO_ENCONTRADO: no existe o el residente no está asignado a su obra."""


class RevisionYaRegistrada(ErrorAtipicos):
    """409 REVISION_YA_REGISTRADA."""


class ComentarioInvalido(ErrorAtipicos):
    """422 COMENTARIO_INVALIDO: `mensaje` es la regla concreta (va en `errores[]` con `campo` = `comentario`)."""
