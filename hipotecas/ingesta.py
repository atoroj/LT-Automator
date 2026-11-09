"""Ingesta de mensajes de correo (Gmail o .eml), independiente de la fuente.

Reglas de asociación (no se adivina nunca):
1. Si el asunto contiene UNA referencia de expediente existente -> ese expediente,
   salvo que el remitente pertenezca a otro expediente distinto (conflicto -> incidencia).
2. Si no hay referencia y está activado `asociar_por_remitente`: el remitente debe coincidir
   exactamente con el correo de contacto de UN único expediente abierto.
3. En cualquier otro caso -> bandeja de incidencias.
"""
from __future__ import annotations

import email
import email.policy
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from email.utils import parseaddr, parsedate_to_datetime

from . import expedientes as exps
from .actividad import registrar
from .config import Config
from .db import ahora, todos, transaccion, uno
from .documentos import procesamiento
from .documentos.texto import MotorOCR
from .extraccion.base import ProveedorExtraccion
from .incidencias import crear_incidencia

log = logging.getLogger("hipotecas.ingesta")


@dataclass
class Adjunto:
    nombre: str
    datos: bytes
    parte_id: str
    mime: str = "application/octet-stream"
    inline: bool = False


@dataclass
class Mensaje:
    mensaje_id: str
    origen: str  # gmail | eml
    remitente: str
    asunto: str
    fecha: str
    adjuntos: list[Adjunto] = field(default_factory=list)


@dataclass
class Resultado:
    estado: str  # procesado | incidencia | ya_procesado
    expediente_id: int | None = None
    motivo: str = ""
    documentos: list[int] = field(default_factory=list)


def asociar(conn: sqlite3.Connection, cfg: Config, remitente: str, asunto: str) -> tuple[int | None, str]:
    patron = re.compile(cfg["gmail"]["patron_referencia"], re.IGNORECASE)
    refs = {r.upper() for r in patron.findall(asunto or "")}
    email_rem = parseaddr(remitente or "")[1].strip().lower()

    abiertos = todos(conn, "SELECT * FROM expedientes WHERE estado != 'cerrado'")
    por_remitente = [e for e in abiertos if email_rem and email_rem in exps.emails_de_expediente(conn, e)]

    if len(refs) > 1:
        return None, f"El asunto contiene varias referencias de expediente: {', '.join(sorted(refs))}"
    if len(refs) == 1:
        ref = next(iter(refs))
        exp = exps.por_referencia(conn, ref)
        if not exp:
            return None, f"La referencia {ref} del asunto no corresponde a ningún expediente"
        if exp["estado"] == "cerrado":
            return None, f"El expediente {ref} está cerrado"
        otros = [e for e in por_remitente if e["id"] != exp["id"]]
        remitente_es_de_este = any(e["id"] == exp["id"] for e in por_remitente)
        if otros and not remitente_es_de_este:
            return None, (f"Conflicto: el asunto indica {ref} pero el remitente es contacto de "
                          f"{', '.join(e['referencia'] for e in otros)}")
        return exp["id"], f"referencia {ref} en el asunto"
    if not cfg["gmail"].get("asociar_por_remitente", True):
        return None, "El asunto no contiene referencia de expediente"
    if len(por_remitente) == 1:
        return por_remitente[0]["id"], "remitente registrado como contacto del expediente"
    if len(por_remitente) > 1:
        return None, "El remitente es contacto de varios expedientes abiertos; no se puede decidir"
    return None, "Sin referencia en el asunto y remitente no registrado en ningún expediente"


def _filtrar_adjuntos(cfg: Config, mensaje: Mensaje) -> tuple[list[Adjunto], list[str]]:
    limite = cfg["gmail"].get("ignorar_imagenes_inline_menores_kb", 30) * 1024
    validos, ignorados = [], []
    for a in mensaje.adjuntos:
        if a.inline and a.mime.startswith("image/") and len(a.datos) < limite:
            ignorados.append(a.nombre)
        else:
            validos.append(a)
    return validos, ignorados


def procesar_mensaje(
    conn: sqlite3.Connection, cfg: Config, mensaje: Mensaje, proveedor: ProveedorExtraccion, ocr: MotorOCR
) -> Resultado:
    previo = uno(conn, "SELECT * FROM mensajes WHERE mensaje_id=?", (mensaje.mensaje_id,))
    if previo and previo["estado"] in ("procesado", "incidencia"):
        return Resultado("ya_procesado", previo["expediente_id"], previo["motivo"] or "")

    exp_id, motivo = asociar(conn, cfg, mensaje.remitente, mensaje.asunto)
    exp = exps.obtener(conn, exp_id) if exp_id else None
    adjuntos, ignorados = _filtrar_adjuntos(cfg, mensaje)
    proc_base = dict(origen=mensaje.origen, mensaje_id=mensaje.mensaje_id, remitente=mensaje.remitente[:300],
                     asunto=mensaje.asunto[:500], fecha_correo=mensaje.fecha)

    doc_ids: list[int] = []
    nuevos: list[int] = []
    for a in adjuntos:
        doc_id, es_nuevo = procesamiento.registrar(
            conn, cfg, exp, a.nombre, a.datos, procesamiento.Procedencia(parte_id=a.parte_id, **proc_base)
        )
        doc_ids.append(doc_id)
        if es_nuevo:
            nuevos.append(doc_id)

    estado = "procesado" if exp else "incidencia"
    with transaccion(conn):
        if exp is None:
            crear_incidencia(
                conn, "correo_sin_expediente",
                f"{motivo}. Remitente: {mensaje.remitente[:120]}. Asunto: {mensaje.asunto[:160]}. "
                f"Adjuntos guardados: {len(doc_ids)}.",
                mensaje_id=mensaje.mensaje_id,
            )
        conn.execute(
            "INSERT INTO mensajes(mensaje_id, origen, estado, expediente_id, remitente, asunto, fecha, motivo, intentos, procesado_en) "
            "VALUES (?,?,?,?,?,?,?,?,1,?) ON CONFLICT(mensaje_id) DO UPDATE SET estado=excluded.estado, "
            "expediente_id=excluded.expediente_id, motivo=excluded.motivo, intentos=mensajes.intentos+1, "
            "procesado_en=excluded.procesado_en, ultimo_error=NULL",
            (mensaje.mensaje_id, mensaje.origen, estado, exp_id, mensaje.remitente[:300], mensaje.asunto[:500],
             mensaje.fecha, motivo, ahora()),
        )
        registrar(
            conn, "correo_procesado",
            f"{mensaje.origen} {mensaje.mensaje_id}: {estado}; {motivo}; adjuntos={len(doc_ids)}"
            + (f"; ignorados inline={len(ignorados)}" if ignorados else ""),
            exp_id,
        )
        if exp is not None and not adjuntos:
            registrar(conn, "correo_sin_adjuntos", mensaje.mensaje_id, exp_id)

    # Procesar documentos nuevos (y los que quedaron a medias tras una interrupción)
    for doc_id in doc_ids:
        d = uno(conn, "SELECT estado FROM documentos WHERE id=?", (doc_id,))
        if d and d["estado"] == "recibido":
            procesamiento.procesar(conn, cfg, doc_id, proveedor, ocr)
    return Resultado(estado, exp_id, motivo, doc_ids)


def registrar_error_mensaje(conn: sqlite3.Connection, cfg: Config, mensaje_id: str, origen: str, error: str) -> None:
    max_int = cfg["gmail"].get("max_intentos_por_mensaje", 5)
    conn.execute(
        "INSERT INTO mensajes(mensaje_id, origen, estado, intentos, ultimo_error) VALUES (?,?,'error',1,?) "
        "ON CONFLICT(mensaje_id) DO UPDATE SET intentos=mensajes.intentos+1, ultimo_error=excluded.ultimo_error, "
        "estado=CASE WHEN mensajes.estado IN ('procesado','incidencia') THEN mensajes.estado ELSE 'error' END",
        (mensaje_id, origen, error[:500]),
    )
    fila = uno(conn, "SELECT intentos FROM mensajes WHERE mensaje_id=?", (mensaje_id,))
    if fila and fila["intentos"] >= max_int:
        crear_incidencia(conn, "error_correo", f"El mensaje {mensaje_id} falló {fila['intentos']} veces: {error[:300]}",
                         mensaje_id=mensaje_id)


def debe_procesarse(conn: sqlite3.Connection, cfg: Config, mensaje_id: str) -> bool:
    fila = uno(conn, "SELECT estado, intentos FROM mensajes WHERE mensaje_id=?", (mensaje_id,))
    if not fila:
        return True
    if fila["estado"] in ("procesado", "incidencia"):
        return False
    return fila["intentos"] < cfg["gmail"].get("max_intentos_por_mensaje", 5)


def asignar_incidencia_a_expediente(
    conn: sqlite3.Connection, cfg: Config, incidencia_id: int, exp_id: int,
    proveedor: ProveedorExtraccion, ocr: MotorOCR,
) -> list[int]:
    """Decisión manual: asocia un correo de la bandeja de incidencias a un expediente.

    Los originales de la bandeja se conservan; se registran copias en la entrada del expediente.
    """
    from .incidencias import cerrar_incidencia
    from . import almacenamiento as alm

    inc = uno(conn, "SELECT * FROM incidencias WHERE id=?", (incidencia_id,))
    if not inc or inc["tipo"] != "correo_sin_expediente" or inc["estado"] != "abierta":
        raise ValueError("Incidencia no válida para asignar")
    exp = exps.obtener(conn, exp_id)
    if not exp:
        raise ValueError("Expediente no encontrado")
    docs = todos(conn, "SELECT * FROM documentos WHERE gmail_mensaje_id=? AND expediente_id IS NULL", (inc["gmail_mensaje_id"],))
    nuevos = []
    for d in docs:
        datos = alm.absoluta(cfg, d["ruta_original"]).read_bytes()
        proc = procesamiento.Procedencia(
            origen=d["origen"], mensaje_id=None, parte_id=None, remitente=d["remitente"],
            asunto=d["asunto"], fecha_correo=d["fecha_correo"],
        )
        nuevo_id, _ = procesamiento.registrar(conn, cfg, exp, d["nombre_original"], datos, proc)
        with transaccion(conn):
            conn.execute(
                "UPDATE documentos SET gmail_mensaje_id=?, parte_id=? WHERE id=?",
                (d["gmail_mensaje_id"], f"{d['parte_id']}#asignado", nuevo_id),
            )
            conn.execute("UPDATE documentos SET estado='descartado', notas=? WHERE id=?",
                         (f"Asignado al expediente {exp['referencia']} como documento #{nuevo_id}", d["id"]))
        nuevos.append(nuevo_id)
    with transaccion(conn):
        conn.execute("UPDATE mensajes SET estado='procesado', expediente_id=?, motivo=? WHERE mensaje_id=?",
                     (exp_id, f"asignado manualmente a {exp['referencia']}", inc["gmail_mensaje_id"]))
        cerrar_incidencia(conn, incidencia_id, "resuelta", f"Asignado manualmente a {exp['referencia']}")
        registrar(conn, "correo_asignado_manual", f"incidencia #{incidencia_id}", exp_id)
    for doc_id in nuevos:
        d = uno(conn, "SELECT estado FROM documentos WHERE id=?", (doc_id,))
        if d and d["estado"] == "recibido":
            procesamiento.procesar(conn, cfg, doc_id, proveedor, ocr)
    return nuevos


# ---------------- Fuente .eml (pruebas sin Gmail y correos guardados) ----------------

def mensaje_desde_eml(datos: bytes) -> Mensaje:
    msg = email.message_from_bytes(datos, policy=email.policy.default)
    mid = (msg.get("Message-ID") or "").strip()
    if not mid:
        import hashlib

        mid = "eml-" + hashlib.sha256(datos).hexdigest()[:24]
    fecha = ""
    if msg.get("Date"):
        try:
            fecha = parsedate_to_datetime(msg["Date"]).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            fecha = str(msg["Date"])[:40]
    adjuntos = []
    for i, parte in enumerate(msg.walk()):
        if parte.is_multipart():
            continue
        nombre = parte.get_filename()
        disp = (parte.get_content_disposition() or "").lower()
        if not nombre and disp != "attachment":
            continue
        contenido = parte.get_payload(decode=True) or b""
        adjuntos.append(Adjunto(
            nombre=nombre or f"adjunto_{i}", datos=contenido, parte_id=f"eml-{i}",
            mime=parte.get_content_type(), inline=(disp == "inline" or bool(parte.get("Content-ID"))),
        ))
    return Mensaje(
        mensaje_id=mid, origen="eml", remitente=str(msg.get("From", "")), asunto=str(msg.get("Subject", "")),
        fecha=fecha, adjuntos=adjuntos,
    )
