"""Conector de Gmail: OAuth (solo lectura) y lectura de mensajes con una etiqueta concreta.

Permiso solicitado: https://www.googleapis.com/auth/gmail.readonly
- Permite leer mensajes y adjuntos. No permite enviar, borrar ni modificar correos.
- La aplicación solo consulta los mensajes que tienen la etiqueta configurada (labelIds),
  nunca recorre el resto del buzón.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path

from ..config import Config
from ..ingesta import Adjunto, Mensaje
from .almacen_token import AlmacenToken

log = logging.getLogger("hipotecas.gmail")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailNoConfigurado(RuntimeError):
    pass


def ruta_credenciales(cfg: Config) -> Path:
    return cfg.dir_secretos / "credentials.json"


def almacen(cfg: Config) -> AlmacenToken:
    return AlmacenToken(cfg.dir_secretos / "gmail_token.bin")


def cargar_credenciales(cfg: Config):
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    import json

    a = almacen(cfg)
    info = a.cargar()
    if not info:
        raise GmailNoConfigurado("Gmail no está conectado")
    creds = Credentials.from_authorized_user_info(json.loads(info), SCOPES)
    if set(creds.scopes or SCOPES) - set(SCOPES):
        raise GmailNoConfigurado("El token tiene permisos distintos de gmail.readonly; vuelve a conectar")
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                raise GmailNoConfigurado("La autorización de Gmail ha caducado o se ha revocado; vuelve a conectar") from e
            a.guardar(creds.to_json())
        else:
            raise GmailNoConfigurado("Token de Gmail no válido; vuelve a conectar")
    return creds


class AutorizacionGmail:
    """Autorización OAuth de Gmail que funciona igual en el PC y en un servidor sin navegador.

    1. `iniciar()` devuelve el enlace de Google; la usuaria lo abre en su navegador y acepta.
    2. Google redirige a http://127.0.0.1:<puerto>/?code=...
       - En el PC esa dirección es la propia aplicación y se completa sola.
       - En el servidor esa página no carga en tu dispositivo: copias la dirección de la barra del
         navegador y la pegas en la pantalla «Entrada» (`completar()`).
    Cambiar de cuenta de Google = desconectar y volver a autorizar con la otra cuenta.
    """

    def __init__(self):
        self.flujo = None
        self.url = ""
        self.estado_oauth = ""
        self.error = ""

    def iniciar(self, cfg: Config) -> str:
        from google_auth_oauthlib.flow import Flow

        cred = ruta_credenciales(cfg)
        if not cred.exists():
            raise GmailNoConfigurado("Primero sube el archivo de credenciales OAuth (credentials.json) de Google Cloud.")
        redireccion = f"http://127.0.0.1:{cfg['servidor']['puerto']}/"
        self.flujo = Flow.from_client_secrets_file(str(cred), SCOPES, redirect_uri=redireccion)
        self.url, self.estado_oauth = self.flujo.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="false"
        )
        self.error = ""
        return self.url

    @property
    def pendiente(self) -> bool:
        return self.flujo is not None

    def completar(self, cfg: Config, url_o_codigo: str) -> None:
        """Acepta la dirección completa a la que redirigió Google o solo el código."""
        from urllib.parse import parse_qs, urlsplit

        if self.flujo is None:
            raise GmailNoConfigurado("No hay ninguna autorización en curso: pulsa «Conectar Gmail» otra vez")
        texto = (url_o_codigo or "").strip()
        if "code=" in texto:
            q = parse_qs(urlsplit(texto).query or texto.split("?", 1)[-1])
            if q.get("error"):
                raise GmailNoConfigurado(f"Google rechazó la autorización: {q['error'][0]}")
            if q.get("state", [""])[0] != self.estado_oauth:
                raise GmailNoConfigurado("La dirección no corresponde a la última autorización solicitada")
            codigo = q.get("code", [""])[0]
        else:
            codigo = texto
        if not codigo:
            raise GmailNoConfigurado("No se ha encontrado el código de autorización")
        try:
            self.flujo.fetch_token(code=codigo)
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {str(e)[:200]}"
            raise GmailNoConfigurado(f"No se pudo completar la autorización ({type(e).__name__})") from e
        creds = self.flujo.credentials
        if not creds.refresh_token:
            raise GmailNoConfigurado("Google no devolvió un token de actualización; revoca el acceso y repite")
        almacen(cfg).guardar(creds.to_json())
        self.flujo, self.url, self.estado_oauth, self.error = None, "", "", ""


def guardar_credenciales(cfg: Config, contenido: bytes) -> None:
    """Valida y guarda el credentials.json (cliente OAuth de tipo «Aplicación de escritorio»)."""
    import json
    import os

    try:
        datos = json.loads(contenido.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise GmailNoConfigurado("El archivo no es un JSON válido") from e
    cliente = datos.get("installed")
    if not isinstance(cliente, dict) or not cliente.get("client_id") or not cliente.get("client_secret"):
        raise GmailNoConfigurado("Debe ser el JSON de un cliente OAuth de tipo «Aplicación de escritorio»")
    ruta = ruta_credenciales(cfg)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_bytes(contenido)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, ruta)  # credencial reemplazable (cambio de cuenta o de proyecto)


class ClienteGmail:
    def __init__(self, cfg: Config, servicio=None):
        self.cfg = cfg
        if servicio is None:
            from googleapiclient.discovery import build

            servicio = build("gmail", "v1", credentials=cargar_credenciales(cfg), cache_discovery=False)
        self.s = servicio

    def id_etiqueta(self, nombre: str) -> str | None:
        etiquetas = self.s.users().labels().list(userId="me").execute().get("labels", [])
        for e in etiquetas:
            if e.get("name") == nombre:
                return e["id"]
        return None

    def ids_con_etiqueta(self, label_id: str, maximo: int = 500) -> list[str]:
        return self._listar(maximo, labelIds=[label_id])

    def ids_por_consulta(self, consulta: str, maximo: int = 200) -> list[str]:
        """Mensajes que cumplen una búsqueda de Gmail (p. ej. remitentes concretos con adjuntos)."""
        return self._listar(maximo, q=consulta)

    def _listar(self, maximo: int, **filtro) -> list[str]:
        ids, token = [], None
        while len(ids) < maximo:
            r = self.s.users().messages().list(
                userId="me", pageToken=token, maxResults=min(100, maximo - len(ids)),
                includeSpamTrash=False, **filtro,
            ).execute()
            ids += [m["id"] for m in r.get("messages", [])]
            token = r.get("nextPageToken")
            if not token:
                break
        return ids

    def obtener_mensaje(self, mensaje_id: str) -> Mensaje:
        m = self.s.users().messages().get(userId="me", id=mensaje_id, format="full").execute()
        cab = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        fecha = ""
        if m.get("internalDate"):
            fecha = datetime.fromtimestamp(int(m["internalDate"]) / 1000).isoformat(timespec="seconds")
        adjuntos: list[Adjunto] = []
        self._recorrer(mensaje_id, m.get("payload", {}), adjuntos)
        return Mensaje(
            mensaje_id=mensaje_id, origen="gmail", remitente=cab.get("from", ""), asunto=cab.get("subject", ""),
            fecha=fecha, adjuntos=adjuntos,
        )

    def _recorrer(self, mensaje_id: str, parte: dict, salida: list[Adjunto]) -> None:
        for sub in parte.get("parts", []) or []:
            self._recorrer(mensaje_id, sub, salida)
        nombre = parte.get("filename")
        cuerpo = parte.get("body", {}) or {}
        if not nombre or not (cuerpo.get("attachmentId") or cuerpo.get("data")):
            return
        cab = {h["name"].lower(): h["value"] for h in parte.get("headers", [])}
        if cuerpo.get("attachmentId"):
            r = self.s.users().messages().attachments().get(
                userId="me", messageId=mensaje_id, id=cuerpo["attachmentId"]
            ).execute()
            datos = base64.urlsafe_b64decode(r["data"].encode("ascii"))
        else:
            datos = base64.urlsafe_b64decode(cuerpo["data"].encode("ascii"))
        disp = cab.get("content-disposition", "").lower()
        salida.append(Adjunto(
            nombre=nombre, datos=datos,
            parte_id=str(parte.get("partId", "")) + ":" + nombre,  # partId es estable; attachmentId no
            mime=parte.get("mimeType", "application/octet-stream"),
            inline=disp.startswith("inline") or "content-id" in cab,
        ))
