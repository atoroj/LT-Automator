"""Conector de Gmail: OAuth (solo lectura) y lectura de mensajes con una etiqueta concreta.

Permiso solicitado: https://www.googleapis.com/auth/gmail.readonly
- Permite leer mensajes y adjuntos. No permite enviar, borrar ni modificar correos.
- La aplicación solo consulta los mensajes que tienen la etiqueta configurada (labelIds),
  nunca recorre el resto del buzón.
"""
from __future__ import annotations

import base64
import logging
import threading
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


class AutorizacionEnCurso:
    """Ejecuta el flujo OAuth de aplicación instalada en segundo plano (abre el navegador)."""

    def __init__(self):
        self.hilo: threading.Thread | None = None
        self.estado = "inactivo"
        self.error = ""

    def iniciar(self, cfg: Config) -> None:
        if self.hilo and self.hilo.is_alive():
            return
        cred = ruta_credenciales(cfg)
        if not cred.exists():
            raise GmailNoConfigurado(f"No se encuentra {cred}. Sigue los pasos del README para crearlo.")

        def trabajo():
            from google_auth_oauthlib.flow import InstalledAppFlow

            try:
                self.estado, self.error = "esperando", ""
                flujo = InstalledAppFlow.from_client_secrets_file(str(cred), SCOPES)
                creds = flujo.run_local_server(
                    host="127.0.0.1", bind_addr="127.0.0.1", port=0, open_browser=True,
                    authorization_prompt_message="", success_message="Autorización completada. Puedes cerrar esta pestaña.",
                    timeout_seconds=300, access_type="offline", prompt="consent",
                )
                if not creds or not creds.refresh_token:
                    raise RuntimeError("Google no devolvió un token de actualización")
                almacen(cfg).guardar(creds.to_json())
                self.estado = "completado"
            except Exception as e:  # noqa: BLE001
                log.warning("Fallo de autorización OAuth: %s", type(e).__name__)
                self.estado, self.error = "error", f"{type(e).__name__}: {str(e)[:200]}"

        self.hilo = threading.Thread(target=trabajo, name="oauth-gmail", daemon=True)
        self.hilo.start()


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
