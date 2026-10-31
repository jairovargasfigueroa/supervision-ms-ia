"""Obras, Materiales, Finanzas y Keycloak simulados con `httpx.MockTransport` y los datos de la 011.

Copia de `docs/datos-prueba.md` (secciones 5 a 9) con D = 2026-10-28: 3 obras, 15 materiales, 5 planillas, 3 cobros y
67 movimientos.
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

ISSUER = "http://keycloak.prueba/realms/supervision"
URL_TOKEN = f"{ISSUER}/protocol/openid-connect/token"
OBRAS = "http://obras.prueba"
MATERIALES = "http://materiales.prueba"
FINANZAS = "http://finanzas.prueba"

OBRA = {o: f"b0000000-0000-4000-8000-00000000000{o}" for o in (1, 2, 3)}
USUARIO = {n: f"a0000000-0000-4000-8000-00000000000{n}" for n in range(1, 10)}


def partida(obra: int, numero: int) -> str:
    return f"b1000000-0000-4000-8000-000000000{obra}{numero:02d}"


def planilla_id(obra: int, numero: int) -> str:
    return f"d0000000-0000-4000-8000-000000000{obra}{numero:02d}"


def cobro_id(obra: int, numero: int) -> str:
    return f"c2000000-0000-4000-8000-000000000{obra}{numero:02d}"


def movimiento_id(numero: int) -> str:
    return f"f0000000-0000-4000-8000-0000000{numero:05d}"


# Sección 5: (código, precio referencial).
CATALOGO = [
    ("CEM-IP30", 62.00),
    ("FIE-12", 8.60),
    ("FIE-10", 8.70),
    ("FIE-06", 9.10),
    ("ALA-16", 14.50),
    ("ARE-FIN", 110.00),
    ("ARE-GRU", 130.00),
    ("GRA-34", 185.00),
    ("PIE-MAN", 150.00),
    ("LAD-6H", 1.30),
    ("MAD-ENC", 7.50),
    ("CLA-25", 16.00),
    ("CER-4545", 78.00),
    ("TUB-PVC4", 95.00),
    ("LAD-18H", 2.10),
]
MATERIAL = {codigo: f"e0000000-0000-4000-8000-0000000000{n:02d}" for n, (codigo, _) in enumerate(CATALOGO, start=1)}
PRECIO = {MATERIAL[codigo]: precio for codigo, precio in CATALOGO}

# Sección 6.
OBRAS_011: dict[int, dict[str, Any]] = {
    1: {
        "nombre": "Edificio Residencial Las Palmeras",
        "ubicacion": "Av. Beni entre 3er y 4to anillo, Santa Cruz de la Sierra",
        "zona": "NORTE",
        "fechaInicio": "2026-06-30",
        "asignaciones": (3, 5, 6, 7),
        "contrato": (2773800.00, 360, 10, 7),
        "avancePct": 28.49,
        "plazoPct": 33.33,
    },
    2: {
        "nombre": "Condominio Las Lomas (12 viviendas)",
        "ubicacion": "Av. Virgen de Cotoca, 6to anillo, Santa Cruz de la Sierra",
        "zona": "ESTE",
        "fechaInicio": "2026-05-31",
        "asignaciones": (4, 5, 6, 9),
        "contrato": (3185140.00, 300, 15, 7),
        "avancePct": 16.34,
        "plazoPct": 50.00,
    },
    3: {
        "nombre": "Unidad Educativa Nueva Esperanza",
        "ubicacion": "Plan Tres Mil, Distrito 8, Santa Cruz de la Sierra",
        "zona": "SUR",
        "fechaInicio": "2026-10-23",
        "asignaciones": (9, 5, 6, 7),
        "contrato": (1900140.00, 240, 10, 5),
        "avancePct": 0.00,
        "plazoPct": 2.08,
    },
}

# Sección 7: (obra, número, periodo, estado, [(partida, monto)], neto, cobro, observación).
OBSERVACION_202 = (
    "La medición de columnas y vigas (40 m³) no coincide con lo verificado en la visita de obra, que fue de unos "
    "30 m³. Corrijan la cantidad y adjunten fotos de los vaciados."
)
PLANILLAS_011: list[tuple[int, int, str, str, list[tuple[int, float]], float, bool, str | None]] = [
    (1, 1, "2026-07", "APROBADA", [(1, 55800.00), (2, 293760.00)], 290134.80, True, None),
    (1, 2, "2026-08", "APROBADA", [(2, 146880.00), (3, 168250.00), (4, 125550.00)], 365764.40, True, None),
    (1, 3, "2026-09", "PRESENTADA", [(3, 134600.00), (4, 209250.00), (5, 53920.00)], 330149.10, False, None),
    (2, 1, "2026-08", "APROBADA", [(1, 28560.00), (2, 256100.00), (3, 235680.00)], 405865.20, True, None),
    (2, 2, "2026-09", "OBSERVADA", [(4, 131600.00)], 102648.00, False, OBSERVACION_202),
]

# Sección 8: (obra, número de planilla, número de cobro, neto, emitido, vence, estado el día D).
COBROS_011 = [
    (1, 1, "COB-2026-0001", 290134.80, "2026-09-18", "2026-10-18", "VENCIDO"),
    (2, 1, "COB-2026-0002", 405865.20, "2026-10-08", "2026-11-07", "PENDIENTE"),
    (1, 2, "COB-2026-0003", 365764.40, "2026-10-16", "2026-11-15", "PENDIENTE"),
]

# Sección 9: (N.º, fecha «AAAA-MM-DD», tipo, material, cantidad, partida). M-3 = 2026-07, M-2 = 2026-08, M-1 = 2026-09.
_MOV = """
10001 2026-07-05 I CEM-IP30 900 -
10002 2026-07-05 I ARE-GRU 65 -
10003 2026-07-05 I GRA-34 100 -
10004 2026-07-06 I FIE-12 10000 -
10005 2026-07-12 S FIE-12 9500 2
10006 2026-07-19 S CEM-IP30 830 2
10007 2026-07-19 S ARE-GRU 59 2
10008 2026-07-19 S GRA-34 95 2
10009 2026-08-03 I CEM-IP30 1100 -
10010 2026-08-03 I ARE-GRU 75 -
10011 2026-08-03 I GRA-34 125 -
10012 2026-08-04 I FIE-12 11000 -
10013 2026-08-04 I FIE-10 3000 -
10014 2026-08-10 S CEM-IP30 415 2
10015 2026-08-10 S ARE-GRU 30 2
10016 2026-08-10 S GRA-34 47 2
10017 2026-08-10 S FIE-12 4750 2
10018 2026-08-17 S FIE-12 6100 3
10019 2026-08-20 S CEM-IP30 380 3
10020 2026-08-20 S ARE-GRU 25 3
10021 2026-08-20 S GRA-34 40 3
10022 2026-08-25 S FIE-10 2650 4
10023 2026-08-26 S CEM-IP30 320 4
10024 2026-08-26 S ARE-GRU 23 4
10025 2026-08-26 S GRA-34 36 4
10026 2026-09-02 I CEM-IP30 1100 -
10027 2026-09-02 I ARE-GRU 80 -
10028 2026-09-02 I GRA-34 110 -
10029 2026-09-03 I FIE-12 6500 -
10030 2026-09-03 I FIE-10 5000 -
10031 2026-09-04 I LAD-6H 15000 -
10032 2026-09-04 I ARE-FIN 20 -
10033 2026-09-09 S FIE-12 6000 3
10034 2026-09-14 S CEM-IP30 305 3
10035 2026-09-14 S ARE-GRU 20 3
10036 2026-09-14 S GRA-34 32 3
10037 2026-09-18 S FIE-10 4450 4
10038 2026-09-21 S CEM-IP30 530 4
10039 2026-09-21 S ARE-GRU 38 4
10040 2026-09-21 S GRA-34 60 4
10041 2026-09-23 S LAD-6H 12100 5
10042 2026-09-23 S CEM-IP30 82 5
10043 2026-09-23 S ARE-FIN 12 5
20001 2026-08-04 I CEM-IP30 1500 -
20002 2026-08-04 I ARE-GRU 120 -
20003 2026-08-04 I GRA-34 160 -
20004 2026-08-04 I PIE-MAN 150 -
20005 2026-08-05 I FIE-12 8000 -
20006 2026-08-11 S CEM-IP30 735 2
20007 2026-08-11 S ARE-GRU 66 2
20008 2026-08-11 S GRA-34 80 2
20009 2026-08-11 S PIE-MAN 145 2
20010 2026-08-16 S FIE-12 7700 3
20011 2026-08-22 S CEM-IP30 680 3
20012 2026-08-22 S ARE-GRU 49 3
20013 2026-08-22 S GRA-34 77 3
20014 2026-09-03 I ARE-GRU 40 -
20015 2026-09-03 I GRA-34 39 -
20016 2026-09-06 I FIE-12 5500 -
20017 2026-09-08 S FIE-12 4850 4
20018 2026-09-10 S CEM-IP30 120 4
20019 2026-09-12 I CEM-IP30 250 -
20020 2026-09-18 S CEM-IP30 190 4
20021 2026-09-18 S ARE-GRU 21 4
20022 2026-09-18 S GRA-34 33 4
30001 2026-10-25 I CEM-IP30 300 -
30002 2026-10-26 I FIE-12 2000 -
"""


def _movimientos() -> dict[int, list[dict[str, Any]]]:
    por_obra: dict[int, list[dict[str, Any]]] = {1: [], 2: [], 3: []}
    for linea in _MOV.strip().splitlines():
        numero, dia, tipo, codigo, cantidad, part = linea.split()
        obra = int(numero[0])
        por_obra[obra].append(
            {
                "id": movimiento_id(int(numero)),
                "materialId": MATERIAL[codigo],
                "tipo": "INGRESO" if tipo == "I" else "SALIDA",
                "cantidad": float(cantidad),
                "partidaId": None if part == "-" else partida(obra, int(part)),
                "periodo": dia[:7],
                "superaStock": numero == "20018",
                "cerrado": dia[:7] in ({"2026-07", "2026-08"} if obra == 1 else {"2026-08"}),
                "registradoPor": USUARIO[6],
                "registradoEn": f"{dia}T14:00:00Z",
            }
        )
    return por_obra


MOVIMIENTOS_011 = _movimientos()


@dataclass
class Peticion:
    metodo: str
    url: str
    encabezados: dict[str, str]


@dataclass
class Servicios011:
    """Responde como los tres servicios y Keycloak (y registra cada petición)."""

    peticiones: list[Peticion] = field(default_factory=list)
    planillas: list[tuple[int, int, str, str, list[tuple[int, float]], float, bool, str | None]] = field(
        default_factory=lambda: list(PLANILLAS_011)
    )
    sin_acceso_materiales: set[str] = field(default_factory=set)
    cobros_con_fallo: set[str] = field(default_factory=set)
    """Obras cuyo `GET /cobros?obraId=` responde 503."""
    expira_en: int = 300
    tokens_emitidos: int = 0

    def transporte(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._responder)

    def _responder(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(Peticion(peticion.method, str(peticion.url), dict(peticion.headers)))
        url = peticion.url
        base = f"{url.scheme}://{url.host}"
        ruta = url.path
        if str(url).startswith(URL_TOKEN):
            return self._token(peticion)
        servicio = {OBRAS: "Obras", MATERIALES: "Materiales", FINANZAS: "Finanzas"}[base]
        if not peticion.headers.get("authorization", "").startswith("Bearer token-"):
            return httpx.Response(401, json={"codigo": "NO_AUTENTICADO"})
        params = {k: v[0] for k, v in parse_qs(url.query.decode()).items()}
        cuerpo = getattr(self, f"_{servicio.lower()}")(ruta, params)
        if isinstance(cuerpo, httpx.Response):
            return cuerpo
        return httpx.Response(200, json=cuerpo)

    def _token(self, peticion: httpx.Request) -> httpx.Response:
        formulario = parse_qs(peticion.content.decode())
        if formulario.get("grant_type") != ["client_credentials"] or formulario.get("client_id") != ["ia-reportes"]:
            return httpx.Response(400, json={"error": "invalid_request"})
        self.tokens_emitidos += 1
        return httpx.Response(200, json={"access_token": f"token-{self.tokens_emitidos}", "expires_in": self.expira_en})

    # Obras

    def _obras(self, ruta: str, _: dict[str, str]) -> Any:
        partes = ruta.strip("/").split("/")
        if partes == ["obras"]:
            return [{"id": OBRA[o], "nombre": d["nombre"], "estado": "EN_EJECUCION"} for o, d in OBRAS_011.items()]
        if len(partes) == 2 and partes[0] == "obras":
            return self._detalle_obra(partes[1])
        if len(partes) == 3 and partes[2] == "planillas":
            obra = _numero_obra(partes[1])
            return [
                {"id": planilla_id(o, n), "numero": n, "periodo": p, "estado": e, "neto": neto}
                for (o, n, p, e, _, neto, _, _) in self.planillas
                if o == obra
            ]
        if len(partes) == 2 and partes[0] == "planillas":
            for o, n, p, e, lineas, neto, con_cobro, observacion in self.planillas:
                if planilla_id(o, n) == partes[1]:
                    return {
                        "id": planilla_id(o, n),
                        "obraId": OBRA[o],
                        "numero": n,
                        "periodo": p,
                        "estado": e,
                        "version": 1,
                        "lineas": [{"partidaId": partida(o, pp), "monto": monto} for pp, monto in lineas],
                        "neto": neto,
                        "observacion": observacion,
                        "cobroId": cobro_id(o, n) if con_cobro else None,
                        "fotoIds": [],
                    }
        return httpx.Response(404, json={"codigo": "RUTA_NO_ENCONTRADA"})

    def _detalle_obra(self, id_: str) -> Any:
        obra = _numero_obra(id_)
        d = OBRAS_011[obra]
        residente, maestro, almacenero, cliente = d["asignaciones"]
        monto, plazo, anticipo, retencion = d["contrato"]
        return {
            "id": id_,
            "nombre": d["nombre"],
            "ubicacion": d["ubicacion"],
            "zona": d["zona"],
            "estado": "EN_EJECUCION",
            "motivoRechazoContrato": None,
            "fechaInicio": d["fechaInicio"],
            "asignaciones": {
                "residenteId": USUARIO[residente],
                "maestroId": USUARIO[maestro],
                "almaceneroId": USUARIO[almacenero],
                "clienteId": USUARIO[cliente],
            },
            "contrato": {
                "contratoId": f"c0000000-0000-4000-8000-00000000000{obra}",
                "monto": monto,
                "plazoDias": plazo,
                "anticipoPct": anticipo,
                "retencionPct": retencion,
            },
            "avancePct": d["avancePct"],
            "plazoPct": d["plazoPct"],
            "enRiesgo": False,
            "partidas": [],
        }

    # Materiales

    def _materiales(self, ruta: str, params: dict[str, str]) -> Any:
        partes = ruta.strip("/").split("/")
        if partes == ["materiales"]:
            materiales = [
                {"id": MATERIAL[c], "codigo": c, "precioReferencial": p, "activo": c != "LAD-18H"} for c, p in CATALOGO
            ]
            return _pagina(materiales, params)
        if len(partes) == 3 and partes[2] == "movimientos":
            assert params.get("orden") == "registradoEn" and params.get("direccion") == "asc"
            assert "periodo" not in params
            if partes[1] in self.sin_acceso_materiales:
                return httpx.Response(404, json={"codigo": "OBRA_NO_ENCONTRADA"})
            return _pagina(MOVIMIENTOS_011[_numero_obra(partes[1])], params)
        return httpx.Response(404, json={"codigo": "RUTA_NO_ENCONTRADA"})

    # Finanzas

    def _finanzas(self, ruta: str, params: dict[str, str]) -> Any:
        if ruta != "/cobros":
            return httpx.Response(404, json={"codigo": "RUTA_NO_ENCONTRADA"})
        if params["obraId"] in self.cobros_con_fallo:
            return httpx.Response(503, json={"codigo": "SERVICIO_NO_DISPONIBLE"})
        obra = _numero_obra(params["obraId"])
        cobros = [
            {
                "id": cobro_id(o, n),
                "numero": numero,
                "obraId": OBRA[o],
                "planillaId": planilla_id(o, n),
                "montoNeto": neto,
                "emitidoEn": emitido,
                "venceEn": vence,
                "estado": estado,
            }
            for (o, n, numero, neto, emitido, vence, estado) in COBROS_011
            if o == obra
        ]
        return _pagina(cobros, params)

    def peticiones_a_servicios(self) -> list[Peticion]:
        return [p for p in self.peticiones if not p.url.startswith(URL_TOKEN)]

    def contar(self, metodo: str, fragmento: str) -> int:
        return sum(1 for p in self.peticiones if p.metodo == metodo and fragmento in p.url)


def _numero_obra(id_: str) -> int:
    return int(id_[-1])


def _pagina(elementos: list[dict[str, Any]], params: dict[str, str]) -> dict[str, Any]:
    pagina = int(params.get("pagina", "1"))
    tamano = int(params.get("tamano", "20"))
    assert tamano <= 100
    return {
        "items": elementos[(pagina - 1) * tamano : pagina * tamano],
        "total": len(elementos),
        "pagina": pagina,
        "tamano": tamano,
    }
