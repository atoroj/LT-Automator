"""Aplicación web local (solo 127.0.0.1)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from .. import catalogo, formato
from ..actividad import configurar_logs
from ..config import Config
from ..db import conectar, inicializar
from ..documentos.texto import MotorOCR
from ..extraccion.servicio import obtener_proveedor
from ..gmail.conector import AutorizacionGmail
from ..carpeta_entrada import VigilanteEntrada
from ..gmail.sincronizador import Sincronizador

DIR = Path(__file__).parent
METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}


class ProteccionLocal(BaseHTTPMiddleware):
    """Defensa frente a DNS rebinding y peticiones entre sitios (CSRF).

    - Solo se aceptan peticiones con Host de loopback o de `[servidor] hosts_permitidos`
      (p. ej. el nombre de Tailscale cuando la aplicación está en el servidor).
    - Las peticiones que modifican datos deben venir de la propia aplicación (Origin/Referer).
    """

    def __init__(self, app, hosts: set[str]):
        super().__init__(app)
        self.hosts = hosts

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in self.hosts:
            return PlainTextResponse("Host no permitido", status_code=400)
        if request.method not in METODOS_SEGUROS:
            origen = request.headers.get("origin") or request.headers.get("referer") or ""
            p = urlsplit(origen)
            if not origen or p.netloc not in self.hosts or p.scheme not in ("http", "https"):
                return PlainTextResponse("Petición rechazada: origen no permitido", status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Cache-Control", "no-store")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "object-src 'self'; frame-ancestors 'self'; form-action 'self'",
        )
        return resp


def redirigir(url: str, ok: str = "", error: str = "") -> RedirectResponse:
    sep = "&" if "?" in url else "?"
    if ok:
        url += f"{sep}ok={quote(ok)}"
    elif error:
        url += f"{sep}error={quote(error)}"
    return RedirectResponse(url, status_code=303)


def crear_app(cfg: Config, hosts_extra: tuple[str, ...] = (), iniciar_sync: bool = True) -> FastAPI:
    cfg.asegurar_directorios()
    configurar_logs(cfg.dir_logs)
    conn = conectar(cfg.ruta_db)
    inicializar(conn)
    conn.close()

    ocr = MotorOCR(cfg["ocr"])
    sync = Sincronizador(cfg, ocr)
    entrada = VigilanteEntrada(cfg, ocr, lambda: app.state.proveedor())

    @asynccontextmanager
    async def ciclo(app: FastAPI):
        if iniciar_sync:
            sync.iniciar()      # recupera los mensajes pendientes tras un reinicio
            entrada.iniciar()   # procesa lo que haya en 00 - ENTRADA y sigue vigilando
        yield
        sync.detener()
        entrada.detener()

    app = FastAPI(title="Gestión de expedientes hipotecarios", lifespan=ciclo, docs_url=None, redoc_url=None, openapi_url=None)
    puerto = cfg["servidor"]["puerto"]
    hosts = {f"127.0.0.1:{puerto}", f"localhost:{puerto}", *hosts_extra,
             *(str(h).strip().lower() for h in cfg["servidor"].get("hosts_permitidos", []) if str(h).strip())}
    app.add_middleware(ProteccionLocal, hosts=hosts)
    app.mount("/static", StaticFiles(directory=DIR / "static"), name="static")

    plantillas = Jinja2Templates(directory=DIR / "templates")
    env = plantillas.env
    env.filters |= {"euros": formato.euros, "pct": formato.pct, "fecha": formato.fecha, "fecha_hora": formato.fecha_hora}
    env.globals |= {"cat": catalogo, "valor_campo": formato.valor_campo}

    app.state.cfg = cfg
    app.state.ocr = ocr
    app.state.sync = sync
    app.state.entrada = entrada
    app.state.oauth = AutorizacionGmail()
    app.state.plantillas = plantillas
    app.state.proveedor = lambda: obtener_proveedor(cfg)

    from . import rutas

    app.include_router(rutas.router)
    return app
