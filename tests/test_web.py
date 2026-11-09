"""Interfaz web: navegación, protecciones locales y flujo básico."""
import pytest
from fastapi.testclient import TestClient

from hipotecas.web.app import crear_app

ORIGEN = {"origin": "http://testserver"}


@pytest.fixture
def cliente(cfg):
    from hipotecas.extraccion.anthropic_proveedor import ProveedorAnthropic
    from simulador_ia import ClienteIASimulado

    app = crear_app(cfg, hosts_extra=("testserver",), iniciar_sync=False)
    simulado = ProveedorAnthropic(cfg, cliente=ClienteIASimulado())
    app.state.proveedor = lambda: (simulado, "IA simulada (pruebas)")
    with TestClient(app) as c:
        yield c


def test_rechaza_host_ajeno(cliente):
    assert cliente.get("/", headers={"host": "malicioso.example"}).status_code == 400


def test_rechaza_post_sin_origen_o_de_otro_sitio(cliente):
    datos = {"nombre": "X", "tipo_operacion": "otra"}
    assert cliente.post("/expedientes/nuevo", data=datos).status_code == 403
    assert cliente.post("/expedientes/nuevo", data=datos, headers={"origin": "http://evil.example"}).status_code == 403


def test_flujo_web(cliente, docs_ficticios):
    r = cliente.post("/expedientes/nuevo", headers=ORIGEN, follow_redirects=False, data={
        "nombre": "Web ficticio", "tipo_operacion": "compra_vivienda_habitual", "emails_contacto": "lucia.prueba@ejemplo.invalid",
        "titular_nombre": "Lucía Prueba Ficticia", "titular_perfil": "asalariado_indefinido", "titular_dni": "12345678Z",
    })
    assert r.status_code == 303
    url = r.headers["location"].split("?")[0]
    exp_id = int(url.rsplit("/", 1)[1])

    archivos = [("archivos", (n, docs_ficticios[n].read_bytes(), "application/pdf"))
                for n in ("DNI_Lucia_Prueba.pdf", "contrato_arras.pdf")]
    r = cliente.post(f"/expedientes/{exp_id}/documentos", headers=ORIGEN, files=archivos, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]

    for pestana in ("resumen", "documentos", "datos", "checklist", "calculos", "informes", "actividad"):
        r = cliente.get(f"/expedientes/{exp_id}?pestana={pestana}")
        assert r.status_code == 200, pestana
    assert "12345678Z" in cliente.get(f"/expedientes/{exp_id}?pestana=datos").text

    r = cliente.get("/documentos/1")
    assert r.status_code == 200 and "SHA-256" in r.text
    r = cliente.get("/documentos/1/archivo/original")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"

    # Importar .eml desde remitente desconocido -> incidencias
    from email.message import EmailMessage

    m = EmailMessage()
    m["From"], m["Subject"], m["Message-ID"] = "nadie@otro.invalid", "hola", "<prueba-1@ficticio.invalid>"
    m.set_content("x")
    m.add_attachment(docs_ficticios["nota_simple.pdf"].read_bytes(), maintype="application", subtype="pdf", filename="nota.pdf")
    r = cliente.post("/importar-eml", headers=ORIGEN, files={"archivo": ("c.eml", bytes(m), "message/rfc822")}, follow_redirects=False)
    assert r.headers["location"].startswith("/incidencias")
    assert "sin expediente asociado" in cliente.get("/incidencias").text

    r = cliente.post(f"/expedientes/{exp_id}/informes", headers=ORIGEN, follow_redirects=False)
    assert r.status_code == 303
    assert cliente.get("/informes/1/pdf").content[:4] == b"%PDF"
    r = cliente.post("/informes/1/aprobar", headers=ORIGEN, data={"referencia": "MAL"}, follow_redirects=False)
    assert "error=" in r.headers["location"]

    r = cliente.post("/ia/probar", headers=ORIGEN, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    pagina_ia = cliente.get("/ia").text
    assert "Recibo de salarios" in pagina_ia or "Nómina" in pagina_ia

    for ruta in ("/", "/expedientes", "/gmail", "/requisitos", "/actividad", "/requisitos/exportar", "/ia"):
        assert cliente.get(ruta).status_code == 200, ruta


def test_descarga_no_permite_traversal(cliente):
    assert cliente.get("/documentos/999/archivo/original").status_code == 404
    assert cliente.get("/documentos/1/archivo/..%2F..%2Fsecretos").status_code == 404


def test_volver_no_permite_redireccion_externa(cliente, cfg):
    from hipotecas import datos, expedientes as exps
    from hipotecas.db import conectar

    c = conectar(cfg.ruta_db)
    exp_id = exps.crear_expediente(c, cfg, "R", "otra")
    cid = datos.anadir_manual(c, exp_id, "importe_solicitado", "1000", "declarado")
    for malo in ("//evil.example", "/\\evil.example", "https://evil.example"):
        r = cliente.post(f"/campos/{cid}/descartar", headers=ORIGEN, data={"volver": malo}, follow_redirects=False)
        assert r.headers["location"].startswith(f"/expedientes/{exp_id}"), malo
