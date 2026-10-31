"""Configuración del servicio desde variables de entorno con prefijo `IA_` (o el archivo `.env`)."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    """Lo que usan `ia-api` e `ia-worker`. El secreto de `ia-reportes` no está aquí a propósito."""

    model_config = SettingsConfigDict(env_prefix="IA_", env_file=".env", extra="ignore")

    entorno: Literal["desarrollo", "produccion"] = "desarrollo"
    puerto: int = 8085

    reportes_db_url: str = "postgresql+psycopg://ia:ia@localhost:5433/reportes_db"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "ia_db"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    keycloak_issuer: str = "http://localhost:8080/realms/supervision"
    audiencia: str = "plataforma-api"
    zona_horaria: str = "America/La_Paz"

    # Modelos versionados (402): carpeta `modelos/` del repositorio; versión de `actual.txt` si no se indica.
    modelos_dir: str = "modelos"
    modelo_riesgo_version: str | None = None

    # 403: versión del modelo de consumos atípicos (si falta, la de <IA_MODELOS_DIR>/consumos_atipicos/actual.txt).
    modelo_atipicos_version: str | None = None

    obras_url: str = "http://localhost:8081"
    materiales_url: str = "http://localhost:8082"
    finanzas_url: str = "http://localhost:8083"

    @property
    def registros_json(self) -> bool:
        return self.entorno == "produccion"

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"

    @property
    def token_url(self) -> str:
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/token"


class ConfiguracionCarga(Configuracion):
    """Solo la lee el comando de carga inicial: incluye el cliente confidencial `ia-reportes`."""

    reportes_client_id: str = "ia-reportes"
    reportes_client_secret: SecretStr = SecretStr("")
