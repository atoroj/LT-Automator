"""Funciones para el despliegue en servidor: acceso por Tailscale, OAuth sin navegador y copias."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from hipotecas.copias import copia_diaria_bd
from hipotecas.gmail.conector import AutorizacionGmail, GmailNoConfigurado, almacen, guardar_credenciales, ruta_credenciales
from hipotecas.web.app import crear_app

CREDENCIALES = json.dumps({"installed": {
    "client_id": "ficticio.apps.googleusercontent.com", "client_secret": "secreto-ficticio",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token",
    "redirect_uris": ["http://localhost"],
}}).encode()


def test_acceso_por_nombre_de_tailscale_con_https(cfg):
    cfg.datos["servidor"]["hosts_permitidos"] = ["gestor.tail1234.ts.net"]
    app = crear_app(cfg, iniciar_sync=False)
    with TestClient(app, base_url="https://gestor.tail1234.ts.net") as c:
        assert c.get("/").status_code == 200
        r = c.post("/expedientes/nuevo", data={"nombre": "Remoto", "tipo_operacion": "otra"},
                   headers={"origin": "https://gestor.tail1234.ts.net"}, follow_redirects=False)
        assert r.status_code == 303
        r = c.post("/expedientes/nuevo", data={"nombre": "X", "tipo_operacion": "otra"},
                   headers={"origin": "https://otro.ejemplo"}, follow_redirects=False)
        assert r.status_code == 403
    with TestClient(app, base_url="https://no-permitido.ejemplo") as c:
        assert c.get("/").status_code == 400


def test_credenciales_se_validan(cfg):
    with pytest.raises(GmailNoConfigurado):
        guardar_credenciales(cfg, b"no es json")
    with pytest.raises(GmailNoConfigurado):
        guardar_credenciales(cfg, json.dumps({"web": {"client_id": "x"}}).encode())
    guardar_credenciales(cfg, CREDENCIALES)
    assert ruta_credenciales(cfg).exists()


class FlujoFalso:
    def __init__(self):
        self.codigo = None
        self.credentials = type("C", (), {"refresh_token": "r-ficticio", "to_json": lambda s: '{"token": "t"}'})()

    def fetch_token(self, code):
        self.codigo = code


def test_oauth_pegando_la_direccion(cfg):
    guardar_credenciales(cfg, CREDENCIALES)
    a = AutorizacionGmail()
    url = a.iniciar(cfg)
    assert url.startswith("https://accounts.google.com/") and "gmail.readonly" in url and "access_type=offline" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8765%2F" in url
    flujo = FlujoFalso()
    a.flujo = flujo
    with pytest.raises(GmailNoConfigurado):  # estado distinto: no corresponde a esta autorización
        a.completar(cfg, "http://127.0.0.1:8765/?state=OTRO&code=abc")
    a.completar(cfg, f"http://127.0.0.1:8765/?state={a.estado_oauth}&code=4/abc&scope=x")
    assert flujo.codigo == "4/abc"
    assert almacen(cfg).cargar() == '{"token": "t"}'
    assert not a.pendiente


def test_oauth_vuelta_directa_en_el_pc(cfg):
    guardar_credenciales(cfg, CREDENCIALES)
    app = crear_app(cfg, hosts_extra=("testserver",), iniciar_sync=False)
    with TestClient(app) as c:
        app.state.oauth.iniciar(cfg)
        app.state.oauth.flujo = FlujoFalso()
        r = c.get(f"/?state={app.state.oauth.estado_oauth}&code=4/xyz", follow_redirects=False)
        assert r.status_code == 303 and "Gmail%20conectado" in r.headers["location"]
    assert almacen(cfg).existe()


def test_copia_diaria_bd(cfg, conn):
    assert copia_diaria_bd(cfg, hoy=date(2026, 1, 1))
    assert not copia_diaria_bd(cfg, hoy=date(2026, 1, 1))  # una por día
    assert copia_diaria_bd(cfg, hoy=date(2026, 3, 1))     # la de enero queda fuera de los 30 días
    copias = sorted(p.name for p in (cfg.dir_datos / "copias").glob("*.db"))
    assert copias == ["hipotecas_20260301.db"]


def test_modo_contenedor_escucha_en_todas_las_interfaces(monkeypatch, tmp_path):
    import hipotecas.__main__ as principal

    capturado = {}
    monkeypatch.setenv("HIPOTECAS_CONTENEDOR", "1")
    monkeypatch.setattr("sys.argv", ["hipotecas", "--config", str(tmp_path / "x.toml")])
    monkeypatch.setattr("uvicorn.run", lambda app, host, port, **kw: capturado.update(host=host, port=port))
    monkeypatch.setattr("hipotecas.web.app.crear_app", lambda cfg: object())
    principal.main()
    assert capturado == {"host": "0.0.0.0", "port": 8765}
