"""Usuario del token y roles efectivos (convenciones, «Usuario con varios roles»)."""

from dataclasses import dataclass

ROLES_INTERNOS = frozenset({"admin", "residente", "gerente", "maestro", "almacenero"})
# El servicio de IA atiende solo al ERP: con otro rol, 403 en todo el servicio (permisos, «IA»).
ROLES_DEL_SERVICIO = frozenset({"admin", "gerente", "residente"})


def roles_efectivos(roles: frozenset[str], azp: str | None) -> frozenset[str]:
    """Con `azp = portal` cuentan solo las reglas de `cliente`; con `erp` o `app-obra`, solo los roles internos;
    cualquier otro `azp` (clientes de servicio o sin `azp`) usa los roles del token tal cual."""
    if azp == "portal":
        return roles & {"cliente"}
    if azp in ("erp", "app-obra"):
        return roles & ROLES_INTERNOS
    return roles


@dataclass(frozen=True)
class Usuario:
    id: str
    """`sub` del token."""
    roles: frozenset[str]
    """Roles efectivos (ya filtrados por `azp`)."""
    azp: str | None = None

    def tiene_alguno(self, *roles: str) -> bool:
        return not self.roles.isdisjoint(roles)
