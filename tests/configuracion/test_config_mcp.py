"""Configuración de la 501: hosts del servidor MCP y cliente `ia-agentes` (solo en la configuración del worker)."""

from pydantic import SecretStr

from ia.config import Configuracion, ConfiguracionWorker


def test_hosts_permitidos_del_servidor_mcp() -> None:
    assert Configuracion().hosts_mcp == ["localhost:8085", "127.0.0.1:8085"]
    config = Configuracion(mcp_hosts_permitidos=" localhost:8085, ia-api:8085 ,,")
    assert config.hosts_mcp == ["localhost:8085", "ia-api:8085"]


def test_el_secreto_de_ia_agentes_solo_esta_en_la_configuracion_del_worker() -> None:
    assert not hasattr(Configuracion(), "agentes_client_secret")
    worker = ConfiguracionWorker()
    assert worker.agentes_client_id == "ia-agentes"
    assert worker.agentes_client_secret.get_secret_value() == ""
    assert worker.propio_mcp_url == "http://localhost:8085/mcp"
    assert [worker.obras_mcp_url, worker.materiales_mcp_url, worker.finanzas_mcp_url] == [
        "http://localhost:8081/mcp",
        "http://localhost:8082/mcp",
        "http://localhost:8083/mcp",
    ]


def test_el_secreto_no_aparece_al_mostrar_la_configuracion() -> None:
    worker = ConfiguracionWorker(agentes_client_secret=SecretStr("secreto-de-prueba"))
    assert "secreto-de-prueba" not in repr(worker)
    assert "secreto-de-prueba" not in str(worker.model_dump())
