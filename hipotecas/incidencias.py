"""Bandeja de incidencias: todo lo que requiere decisión humana."""
from __future__ import annotations

import sqlite3

from .actividad import registrar
from .db import ahora, uno

TIPOS_INCIDENCIA = {
    "correo_sin_expediente": "Correo o archivo sin expediente asociado",
    "formato_no_admitido": "Formato no admitido",
    "documento_ilegible": "Documento ilegible o protegido",
    "requiere_ocr": "Requiere OCR (no disponible)",
    "error_proceso": "Error de procesamiento",
    "error_ia": "No se pudo analizar con IA",
    "error_correo": "Error al procesar un correo",
    "documento_en_otro_expediente": "Documento ya presente en otro expediente",
    "tamano_excesivo": "Adjunto demasiado grande",
}


def crear_incidencia(
    conn: sqlite3.Connection,
    tipo: str,
    descripcion: str,
    expediente_id: int | None = None,
    documento_id: int | None = None,
    mensaje_id: str | None = None,
) -> int:
    existente = uno(
        conn,
        "SELECT id FROM incidencias WHERE estado='abierta' AND tipo=? AND documento_id IS ? "
        "AND gmail_mensaje_id IS ? AND expediente_id IS ?",
        (tipo, documento_id, mensaje_id, expediente_id),
    )
    if existente:
        return existente["id"]
    cur = conn.execute(
        "INSERT INTO incidencias(tipo, descripcion, expediente_id, documento_id, gmail_mensaje_id, creado) "
        "VALUES (?,?,?,?,?,?)",
        (tipo, descripcion[:1000], expediente_id, documento_id, mensaje_id, ahora()),
    )
    registrar(conn, "incidencia_creada", f"#{cur.lastrowid} {tipo}", expediente_id, documento_id)
    return cur.lastrowid


def cerrar_incidencia(conn: sqlite3.Connection, incidencia_id: int, estado: str, resolucion: str) -> None:
    if estado not in ("resuelta", "descartada"):
        raise ValueError("Estado no válido")
    inc = uno(conn, "SELECT * FROM incidencias WHERE id=?", (incidencia_id,))
    if not inc:
        raise ValueError("Incidencia no encontrada")
    conn.execute(
        "UPDATE incidencias SET estado=?, resuelto=?, resolucion=? WHERE id=?",
        (estado, ahora(), resolucion[:1000], incidencia_id),
    )
    registrar(conn, f"incidencia_{estado}", f"#{incidencia_id}", inc["expediente_id"], inc["documento_id"])


def contar_abiertas(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM incidencias WHERE estado='abierta'").fetchone()[0]
