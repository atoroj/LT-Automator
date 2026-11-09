"""Carpeta de entrada vigilada: GESTOR HIPOTECARIO\\00 - ENTRADA.

Lo que se deposite aquí (p. ej. documentos descargados de WhatsApp) se procesa exactamente igual
que un adjunto de correo: mismo registro de procedencia, asociación, duplicados e incidencias.

- `00 - ENTRADA\\<EXP-AAAA-NNNN - Nombre>\\archivo.pdf` -> se asocia a ese expediente (la aplicación
  crea automáticamente una subcarpeta por expediente abierto).
- `00 - ENTRADA\\archivo.pdf` -> se asocia solo si el nombre del archivo contiene la referencia;
  si no, va a la bandeja de incidencias para que decidas.
- Cuando el documento queda guardado y verificado (misma huella SHA-256) en su destino, el archivo
  se retira de la carpeta de entrada para no procesarlo dos veces. Si algo falla, se queda donde está.
- Si se deposita otra vez un archivo idéntico, no se reprocesa: se mueve a 03 - INCIDENCIAS\\Entrada duplicada.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import almacenamiento as alm
from .actividad import registrar
from .config import Config
from .db import ahora, conectar, todos
from .documentos.texto import MotorOCR
from .extraccion.base import ProveedorExtraccion
from .ingesta import Adjunto, Mensaje, procesar_mensaje
from .seguridad import ruta_unica, slug

log = logging.getLogger("hipotecas.entrada")

IGNORAR_NOMBRES = {"desktop.ini", "thumbs.db", ".ds_store"}
IGNORAR_SUFIJOS = (".tmp", ".crdownload", ".part", ".partial", ".download", ".lnk")
CARPETA_DUPLICADOS = "Entrada duplicada"


def asegurar_subcarpetas(conn: sqlite3.Connection, cfg: Config) -> None:
    """Crea en 00 - ENTRADA una subcarpeta por cada expediente abierto (si no existe)."""
    for e in todos(conn, "SELECT referencia, nombre FROM expedientes WHERE estado != 'cerrado'"):
        alm.dir_entrada_expediente(cfg, e["referencia"], e["nombre"]).mkdir(parents=True, exist_ok=True)


def _ignorar(ruta: Path) -> bool:
    n = ruta.name.lower()
    return n in IGNORAR_NOMBRES or n.startswith(("~$", ".")) or n.endswith(IGNORAR_SUFIJOS)


def archivos_pendientes(cfg: Config) -> list[Path]:
    if not cfg.dir_entrada.exists():
        return []
    return sorted((p for p in cfg.dir_entrada.rglob("*") if p.is_file() and not _ignorar(p)),
                  key=lambda p: p.stat().st_mtime)


def _mensaje_de_archivo(cfg: Config, ruta: Path, datos: bytes, sha: str) -> Mensaje:
    rel = ruta.relative_to(cfg.dir_entrada)
    subcarpeta = rel.parts[0] if len(rel.parts) > 1 else ""
    return Mensaje(
        # Misma huella en la misma subcarpeta = mismo envío (evita reprocesar el mismo archivo)
        mensaje_id=f"carpeta-{sha[:32]}-{slug(subcarpeta or 'raiz', 40)}",
        origen="carpeta",
        remitente="Carpeta de entrada",
        asunto=f"{subcarpeta} / {ruta.name}" if subcarpeta else ruta.name,  # la referencia se busca aquí
        fecha=datetime.fromtimestamp(ruta.stat().st_mtime).isoformat(timespec="seconds"),
        adjuntos=[Adjunto(nombre=ruta.name, datos=datos, parte_id=ruta.name)],
    )


def _guardado_verificado(conn: sqlite3.Connection, cfg: Config, mensaje_id: str, sha: str) -> bool:
    for d in todos(conn, "SELECT ruta_original, sha256 FROM documentos WHERE gmail_mensaje_id=?", (mensaje_id,)):
        try:
            ruta = alm.absoluta(cfg, d["ruta_original"])
        except ValueError:
            continue
        if d["sha256"] == sha and ruta.is_file() and alm.sha256_archivo(ruta) == sha:
            return True
    return False


def escanear(
    conn: sqlite3.Connection, cfg: Config, proveedor: ProveedorExtraccion | None, ocr: MotorOCR,
    ahora_ts: float | None = None,
) -> dict:
    asegurar_subcarpetas(conn, cfg)
    estabilidad = float(cfg["carpetas"].get("segundos_estabilidad", 5))
    ahora_ts = ahora_ts if ahora_ts is not None else time.time()
    cont = {"procesados": 0, "incidencias": 0, "duplicados": 0, "en_espera": 0, "errores": 0}

    for ruta in archivos_pendientes(cfg):
        try:
            st = ruta.stat()
            if ahora_ts - st.st_mtime < estabilidad:
                cont["en_espera"] += 1  # todavía se está copiando
                continue
            datos = ruta.read_bytes()  # falla si otro programa lo tiene bloqueado: se reintenta luego
        except OSError:
            cont["en_espera"] += 1
            continue
        sha = alm.sha256_bytes(datos)
        msg = _mensaje_de_archivo(cfg, ruta, datos, sha)
        try:
            r = procesar_mensaje(conn, cfg, msg, proveedor, ocr)
        except Exception as e:  # noqa: BLE001 - el archivo se queda en la entrada y se reintenta
            log.exception("Error procesando archivo de la carpeta de entrada")
            registrar(conn, "entrada_error", f"{type(e).__name__}: {str(e)[:200]}")
            cont["errores"] += 1
            continue

        if r.estado == "ya_procesado":
            destino = ruta_unica(cfg.dir_incidencias / CARPETA_DUPLICADOS, ruta.name)
            destino.parent.mkdir(parents=True, exist_ok=True)
            os.replace(ruta, destino)
            registrar(conn, "entrada_duplicada", f"{ruta.name} ya se había procesado; movido a {CARPETA_DUPLICADOS}",
                      r.expediente_id)
            cont["duplicados"] += 1
            continue
        if _guardado_verificado(conn, cfg, msg.mensaje_id, sha):
            ruta.unlink()  # el contenido ya está guardado y verificado en su destino
            registrar(conn, "entrada_retirada", f"{ruta.name} guardado y verificado; retirado de la entrada", r.expediente_id)
        cont["procesados" if r.estado == "procesado" else "incidencias"] += 1
    return cont


@dataclass
class EstadoEntrada:
    ultima_ejecucion: str = ""
    contadores: dict = field(default_factory=dict)   # última revisión
    totales: dict = field(default_factory=dict)      # acumulado desde que arrancó la aplicación
    ultimo_error: str = ""


class VigilanteEntrada:
    """Revisa la carpeta de entrada periódicamente en segundo plano."""

    def __init__(self, cfg: Config, ocr: MotorOCR, proveedor_fn):
        self.cfg, self.ocr, self.proveedor_fn = cfg, ocr, proveedor_fn
        self.estado = EstadoEntrada()
        self._cerrojo = threading.Lock()
        self._parar = threading.Event()
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None

    @property
    def activo(self) -> bool:
        return bool(self._hilo and self._hilo.is_alive())

    def iniciar(self) -> None:
        if self.activo or not self.cfg["carpetas"].get("vigilar_entrada", True):
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="vigilante-entrada", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        self._despertar.set()

    def solicitar_ahora(self) -> None:
        if self.activo:
            self._despertar.set()
        else:
            threading.Thread(target=self.revisar, name="entrada-manual", daemon=True).start()

    def _bucle(self) -> None:
        intervalo = max(3, int(self.cfg["carpetas"].get("intervalo_segundos", 15)))
        while not self._parar.is_set():
            self.revisar()
            self._despertar.wait(intervalo)
            self._despertar.clear()

    def revisar(self) -> dict:
        if not self._cerrojo.acquire(blocking=False):
            return {}
        conn = None
        try:
            conn = conectar(self.cfg.ruta_db)
            proveedor, _ = self.proveedor_fn()
            cont = escanear(conn, self.cfg, proveedor, self.ocr)
            self.estado.contadores, self.estado.ultimo_error = cont, ""
            for k, v in cont.items():
                if k != "en_espera":
                    self.estado.totales[k] = self.estado.totales.get(k, 0) + v
            return cont
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo revisando la carpeta de entrada")
            self.estado.ultimo_error = f"{type(e).__name__}: {str(e)[:200]}"
            return {}
        finally:
            self.estado.ultima_ejecucion = ahora()
            if conn is not None:
                conn.close()
            self._cerrojo.release()
