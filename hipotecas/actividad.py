"""Registro de actividad (auditoría en BD) y configuración de logs técnicos.

Regla: nunca se escribe el contenido de documentos en los logs. Solo identificadores,
tipos de evento y metadatos mínimos.
"""
from __future__ import annotations

import logging
import logging.handlers
import sqlite3
from pathlib import Path

from .db import ahora

log = logging.getLogger("hipotecas")


def registrar(
    conn: sqlite3.Connection,
    tipo: str,
    detalle: str = "",
    expediente_id: int | None = None,
    documento_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO actividad(fecha, tipo, expediente_id, documento_id, detalle) VALUES (?,?,?,?,?)",
        (ahora(), tipo, expediente_id, documento_id, detalle[:500]),
    )
    log.info("evento=%s expediente=%s documento=%s", tipo, expediente_id, documento_id)


def configurar_logs(dir_logs: Path, nivel: int = logging.INFO) -> None:
    dir_logs.mkdir(parents=True, exist_ok=True)
    raiz = logging.getLogger("hipotecas")
    if any(isinstance(h, logging.handlers.RotatingFileHandler) for h in raiz.handlers):
        return
    h = logging.handlers.RotatingFileHandler(
        dir_logs / "aplicacion.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    raiz.addHandler(h)
    raiz.setLevel(nivel)
    # Las librerías de Google pueden registrar URLs con datos; se limitan a avisos.
    for ruidoso in ("googleapiclient", "google_auth_oauthlib", "urllib3", "httpx", "anthropic"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
