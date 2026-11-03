"""CA adicional de las conexiones TLS (601, `IA_CA_EXTRA`): qué recibe cada cliente y que de verdad confía en ella."""

import asyncio
import datetime as dt
import ipaddress
import ssl
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import certifi
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from ia.auth.token import ProveedorJwks
from ia.comun import base_datos, mongo
from ia.comun.tls import (
    argumentos_postgres,
    ca_extra,
    configurar_tls,
    contexto_ssl,
    opciones_mongo,
    verificar_http,
)
from ia.config import Configuracion, ConfiguracionInvalida
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.rabbit import ConexionRabbit


def _nombre(texto: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, texto)])


def _firmar(
    sujeto: str, clave: ec.EllipticCurvePrivateKey, emisor: str, clave_emisor: ec.EllipticCurvePrivateKey, ca: bool
) -> x509.Certificate:
    ahora = dt.datetime.now(dt.UTC)
    constructor = (
        x509.CertificateBuilder()
        .subject_name(_nombre(sujeto))
        .issuer_name(_nombre(emisor))
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - dt.timedelta(minutes=5))
        .not_valid_after(ahora + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        constructor = constructor.add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        ).add_extension(x509.SubjectKeyIdentifier.from_public_key(clave.public_key()), critical=False)
    else:
        constructor = constructor.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        ).add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(clave_emisor.public_key()), critical=False
        )
    return constructor.sign(clave_emisor, hashes.SHA256())


@pytest.fixture
def ca_local(tmp_path: Path) -> Iterator[tuple[Path, Path, Path]]:
    """CA local (como la de Caddy) y un certificado de servidor para 127.0.0.1 firmado por ella."""
    clave_ca = ec.generate_private_key(ec.SECP256R1())
    certificado_ca = _firmar("CA local de prueba", clave_ca, "CA local de prueba", clave_ca, ca=True)
    clave_servidor = ec.generate_private_key(ec.SECP256R1())
    certificado_servidor = _firmar("localhost", clave_servidor, "CA local de prueba", clave_ca, ca=False)
    ruta_ca = tmp_path / "ca.pem"
    ruta_ca.write_bytes(certificado_ca.public_bytes(serialization.Encoding.PEM))
    ruta_cert = tmp_path / "servidor.pem"
    ruta_cert.write_bytes(certificado_servidor.public_bytes(serialization.Encoding.PEM))
    ruta_clave = tmp_path / "servidor.key"
    ruta_clave.write_bytes(
        clave_servidor.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    yield ruta_ca, ruta_cert, ruta_clave
    configurar_tls(None)


@pytest.fixture
async def servidor_https(ca_local: tuple[Path, Path, Path]) -> AsyncIterator[int]:
    """Servidor HTTPS mínimo en 127.0.0.1 con el certificado firmado por la CA local; responde 200 «ok»."""
    _, ruta_cert, ruta_clave = ca_local
    contexto = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    contexto.load_cert_chain(ruta_cert, ruta_clave)

    async def atender(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            await lector.readuntil(b"\r\n\r\n")
            escritor.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await escritor.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            escritor.close()

    servidor = await asyncio.start_server(atender, "127.0.0.1", 0, ssl=contexto)
    puerto = servidor.sockets[0].getsockname()[1]
    try:
        yield puerto
    finally:
        servidor.close()
        await servidor.wait_closed()


def test_sin_ca_extra_cada_libreria_usa_lo_suyo() -> None:
    configurar_tls(None)
    assert ca_extra() is None
    assert contexto_ssl() is None
    assert verificar_http() is True
    assert opciones_mongo("mongodb://m:27017/?tls=true") == {}
    assert argumentos_postgres("postgresql+psycopg://u:c@h:5432/b?sslmode=verify-full") == {}


@pytest.mark.parametrize("valor", ["", "   "])
def test_ca_extra_vacia_es_ninguna(valor: str) -> None:
    configurar_tls(valor)
    assert ca_extra() is None


def test_la_variable_ia_ca_extra_llega_a_la_configuracion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IA_CA_EXTRA", "/certificados/ca.pem")
    assert Configuracion().ca_extra == "/certificados/ca.pem"


def test_ca_extra_que_no_existe_no_deja_arrancar(tmp_path: Path) -> None:
    with pytest.raises(ConfiguracionInvalida, match="IA_CA_EXTRA"):
        configurar_tls(str(tmp_path / "no-existe.pem"))


def test_ca_extra_que_no_es_pem_no_deja_arrancar(tmp_path: Path) -> None:
    archivo = tmp_path / "ca.pem"
    archivo.write_text("esto no es un certificado", encoding="utf-8")
    with pytest.raises(ConfiguracionInvalida, match="IA_CA_EXTRA"):
        configurar_tls(str(archivo))


def test_contexto_con_ca_extra_confia_en_ella_y_en_las_publicas(ca_local: tuple[Path, Path, Path]) -> None:
    configurar_tls(str(ca_local[0]))
    contexto = contexto_ssl()
    assert contexto is not None
    assert verificar_http() is contexto
    assert contexto.verify_mode == ssl.CERT_REQUIRED and contexto.check_hostname
    sujetos = [dict(par[0] for par in c["subject"]).get("commonName") for c in contexto.get_ca_certs()]
    assert "CA local de prueba" in sujetos
    assert len(sujetos) > 100  # más las de certifi


def test_mongo_recibe_un_paquete_de_ca_solo_si_la_url_pide_tls(ca_local: tuple[Path, Path, Path]) -> None:
    configurar_tls(str(ca_local[0]))
    for url in ("mongodb://m:27017/?tls=true", "mongodb://m:27017/?replicaSet=rs0&ssl=TRUE", "mongodb+srv://c.x.net"):
        paquete = Path(opciones_mongo(url)["tlsCAFile"])
        contenido = paquete.read_bytes()
        assert ca_local[0].read_bytes() in contenido
        assert Path(certifi.where()).read_bytes().strip() in contenido
    assert opciones_mongo("mongodb://m:27017/?replicaSet=rs0") == {}
    assert opciones_mongo("mongodb://m:27017/?tls=true&tlsCAFile=/propia.pem") == {}


@pytest.mark.parametrize("sslmode", ["require", "verify-ca", "verify-full"])
def test_postgres_recibe_sslrootcert_si_la_url_pide_tls(ca_local: tuple[Path, Path, Path], sslmode: str) -> None:
    configurar_tls(str(ca_local[0]))
    argumentos = argumentos_postgres(f"postgresql+psycopg://u:c@h:5432/b?sslmode={sslmode}")
    assert ca_local[0].read_bytes() in Path(argumentos["sslrootcert"]).read_bytes()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:c@h:5432/b",
        "postgresql+psycopg://u:c@h:5432/b?sslmode=prefer",
        "postgresql+psycopg://u:c@h:5432/b?sslmode=disable",
        "postgresql+psycopg://u:c@h:5432/b?sslmode=verify-full&sslrootcert=/propia.pem",
    ],
)
def test_postgres_sin_tls_o_con_su_ca_no_cambia(ca_local: tuple[Path, Path, Path], url: str) -> None:
    configurar_tls(str(ca_local[0]))
    assert argumentos_postgres(url) == {}


async def test_httpx_confia_en_la_ca_extra(servidor_https: int, ca_local: tuple[Path, Path, Path]) -> None:
    url = f"https://127.0.0.1:{servidor_https}/salud"
    configurar_tls(None)
    async with httpx.AsyncClient(verify=verificar_http()) as cliente:
        with pytest.raises(httpx.ConnectError):
            await cliente.get(url)
    configurar_tls(str(ca_local[0]))
    async with httpx.AsyncClient(verify=verificar_http()) as cliente:
        respuesta = await cliente.get(url)
    assert respuesta.status_code == 200
    assert respuesta.text == "ok"


async def test_tls_crudo_confia_en_la_ca_extra(servidor_https: int, ca_local: tuple[Path, Path, Path]) -> None:
    """Lo mismo que hacen aio-pika y PyJWKClient con el contexto: el saludo TLS pasa con la CA y falla sin ella."""
    configurar_tls(str(ca_local[0]))
    contexto = contexto_ssl()
    assert contexto is not None
    _, escritor = await asyncio.open_connection("127.0.0.1", servidor_https, ssl=contexto)
    escritor.close()
    with pytest.raises(ssl.SSLCertVerificationError):
        await asyncio.open_connection("127.0.0.1", servidor_https, ssl=ssl.create_default_context(cafile=certifi.where()))


def test_motor_de_postgres_recibe_los_argumentos_tls(
    ca_local: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    recibido: dict[str, Any] = {}
    monkeypatch.setattr(base_datos, "create_async_engine", lambda url, **kw: recibido.update(kw))
    configurar_tls(str(ca_local[0]))
    base_datos.crear_motor("postgresql+psycopg://u:c@h:5432/b?sslmode=verify-full")
    assert "sslrootcert" in recibido["connect_args"]
    base_datos.crear_motor("postgresql+psycopg://u:c@h:5432/b")
    assert recibido["connect_args"] == {}


def test_cliente_mongo_recibe_la_ca(ca_local: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    recibido: dict[str, Any] = {}
    monkeypatch.setattr(mongo, "AsyncMongoClient", lambda url, **kw: recibido.update(kw))
    configurar_tls(str(ca_local[0]))
    mongo.crear_cliente_mongo("mongodb://m:27017/?tls=true")
    assert "tlsCAFile" in recibido


async def test_rabbit_recibe_el_contexto(ca_local: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    configurar_tls(str(ca_local[0]))
    detener = asyncio.Event()
    recibido: dict[str, Any] = {}

    async def conectar(url: str, **kw: Any) -> None:
        recibido.update(kw)
        detener.set()
        raise ConnectionError("sin RabbitMQ en la prueba")

    monkeypatch.setattr("ia.mensajeria.rabbit.aio_pika.connect_robust", conectar)
    conexion = ConexionRabbit("amqps://ia:clave@rabbit:5671/", ConsumidorMensajes())
    assert await conexion.iniciar(detener) is False
    assert recibido["ssl_context"] is contexto_ssl()


def test_jwks_recibe_el_contexto(ca_local: tuple[Path, Path, Path]) -> None:
    configurar_tls(str(ca_local[0]))
    proveedor = ProveedorJwks("https://keycloak.azure.localhost:8443/realms/supervision/protocol/openid-connect/certs")
    assert proveedor._cliente.ssl_context is contexto_ssl()
