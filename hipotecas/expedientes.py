"""Gestión de expedientes y titulares."""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from email.utils import parseaddr

from .actividad import registrar
from .almacenamiento import (
    COLABORADOR_POR_DEFECTO,
    carpeta_nueva_expediente,
    crear_estructura_expediente,
    dir_entrada_expediente,
)
from .catalogo import ESTADOS_EXPEDIENTE, PERFILES_LABORALES, TIPOS_OPERACION
from .config import Config
from .db import ahora, todos, transaccion, uno


class DatosNoValidos(ValueError):
    pass


def _emails(texto: str) -> str:
    vistos = []
    for parte in re.split(r"[,;\s]+", texto or ""):
        email = parseaddr(parte)[1].strip().lower()
        if email and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) and email not in vistos:
            vistos.append(email)
    return ", ".join(vistos)


def _colaborador(valor: str) -> str:
    valor = re.sub(r"\s+", " ", (valor or "").strip())[:60]
    return valor or COLABORADOR_POR_DEFECTO


def colaboradores(conn: sqlite3.Connection) -> list[str]:
    return [r["colaborador"] for r in todos(conn, "SELECT DISTINCT colaborador FROM expedientes ORDER BY colaborador")]


def siguiente_referencia(conn: sqlite3.Connection, prefijo: str) -> str:
    anio = date.today().year
    patron = f"{prefijo}-{anio}-%"
    filas = todos(conn, "SELECT referencia FROM expedientes WHERE referencia LIKE ?", (patron,))
    nums = [int(r["referencia"].rsplit("-", 1)[1]) for r in filas if r["referencia"].rsplit("-", 1)[1].isdigit()]
    return f"{prefijo}-{anio}-{(max(nums) + 1) if nums else 1:04d}"


def crear_expediente(
    conn: sqlite3.Connection, cfg: Config, nombre: str, tipo_operacion: str, emails_contacto: str = "", notas: str = "",
    colaborador: str = COLABORADOR_POR_DEFECTO,
) -> int:
    nombre = (nombre or "").strip()
    if not nombre:
        raise DatosNoValidos("El nombre del expediente es obligatorio")
    if tipo_operacion not in TIPOS_OPERACION:
        raise DatosNoValidos("Tipo de operación no válido")
    with transaccion(conn):
        ref = siguiente_referencia(conn, cfg["expedientes"]["prefijo_referencia"])
        colaborador = _colaborador(colaborador)
        carpeta = carpeta_nueva_expediente(cfg, ref, nombre, colaborador)
        ts = ahora()
        cur = conn.execute(
            "INSERT INTO expedientes(referencia, nombre, tipo_operacion, emails_contacto, colaborador, notas, carpeta, creado, actualizado) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ref, nombre[:200], tipo_operacion, _emails(emails_contacto), colaborador, notas[:5000], carpeta, ts, ts),
        )
        exp_id = cur.lastrowid
        crear_estructura_expediente(cfg, carpeta)
        dir_entrada_expediente(cfg, ref, nombre).mkdir(parents=True, exist_ok=True)
        registrar(conn, "expediente_creado", ref, exp_id)
    return exp_id


def actualizar_expediente(conn: sqlite3.Connection, exp_id: int, **campos) -> None:
    permitidos = {"nombre", "tipo_operacion", "estado", "emails_contacto", "notas", "observaciones_informe", "colaborador"}
    cambios = {k: v for k, v in campos.items() if k in permitidos and v is not None}
    if "tipo_operacion" in cambios and cambios["tipo_operacion"] not in TIPOS_OPERACION:
        raise DatosNoValidos("Tipo de operación no válido")
    if "estado" in cambios and cambios["estado"] not in ESTADOS_EXPEDIENTE:
        raise DatosNoValidos("Estado no válido")
    if "nombre" in cambios and not cambios["nombre"].strip():
        raise DatosNoValidos("El nombre es obligatorio")
    if "colaborador" in cambios:
        # La carpeta del expediente no se mueve: el colaborador queda registrado y filtra las vistas.
        cambios["colaborador"] = _colaborador(cambios["colaborador"])
    if "emails_contacto" in cambios:
        cambios["emails_contacto"] = _emails(cambios["emails_contacto"])
    if not cambios:
        return
    sets = ", ".join(f"{k}=?" for k in cambios)
    conn.execute(f"UPDATE expedientes SET {sets}, actualizado=? WHERE id=?", (*cambios.values(), ahora(), exp_id))
    registrar(conn, "expediente_modificado", ", ".join(sorted(cambios)), exp_id)


def obtener(conn: sqlite3.Connection, exp_id: int) -> sqlite3.Row | None:
    return uno(conn, "SELECT * FROM expedientes WHERE id=?", (exp_id,))


def por_referencia(conn: sqlite3.Connection, referencia: str) -> sqlite3.Row | None:
    return uno(conn, "SELECT * FROM expedientes WHERE UPPER(referencia)=UPPER(?)", (referencia,))


def listar(conn: sqlite3.Connection, buscar: str = "", estado: str = "", colaborador: str = "") -> list[sqlite3.Row]:
    sql = (
        "SELECT e.*, (SELECT COUNT(*) FROM documentos d WHERE d.expediente_id=e.id) AS n_docs, "
        "(SELECT GROUP_CONCAT(t.nombre, ', ') FROM titulares t WHERE t.expediente_id=e.id) AS nombres_titulares "
        "FROM expedientes e WHERE 1=1"
    )
    params: list = []
    if buscar:
        sql += " AND (e.referencia LIKE ? OR e.nombre LIKE ? OR EXISTS (SELECT 1 FROM titulares t WHERE t.expediente_id=e.id AND (t.nombre LIKE ? OR t.dni_nie LIKE ?)))"
        params += [f"%{buscar}%"] * 4
    if estado:
        sql += " AND e.estado=?"
        params.append(estado)
    if colaborador:
        sql += " AND e.colaborador=?"
        params.append(colaborador)
    return todos(conn, sql + " ORDER BY e.actualizado DESC", params)


def titulares(conn: sqlite3.Connection, exp_id: int) -> list[sqlite3.Row]:
    return todos(conn, "SELECT * FROM titulares WHERE expediente_id=? ORDER BY orden, id", (exp_id,))


def anadir_titular(
    conn: sqlite3.Connection, exp_id: int, nombre: str, perfil_laboral: str, dni_nie: str = "", email: str = ""
) -> int:
    if not (nombre or "").strip():
        raise DatosNoValidos("El nombre del titular es obligatorio")
    if perfil_laboral not in PERFILES_LABORALES:
        raise DatosNoValidos("Perfil laboral no válido")
    orden = conn.execute("SELECT COALESCE(MAX(orden),0)+1 FROM titulares WHERE expediente_id=?", (exp_id,)).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO titulares(expediente_id, orden, nombre, dni_nie, email, perfil_laboral, creado) VALUES (?,?,?,?,?,?,?)",
        (exp_id, orden, nombre.strip()[:200], (dni_nie or "").strip().upper().replace("-", "")[:20],
         _emails(email).split(",")[0].strip(), perfil_laboral, ahora()),
    )
    conn.execute("UPDATE expedientes SET actualizado=? WHERE id=?", (ahora(), exp_id))
    registrar(conn, "titular_anadido", f"titular #{cur.lastrowid}", exp_id)
    return cur.lastrowid


def actualizar_titular(conn: sqlite3.Connection, titular_id: int, nombre: str, perfil_laboral: str, dni_nie: str, email: str) -> None:
    if not (nombre or "").strip():
        raise DatosNoValidos("El nombre del titular es obligatorio")
    if perfil_laboral not in PERFILES_LABORALES:
        raise DatosNoValidos("Perfil laboral no válido")
    t = uno(conn, "SELECT * FROM titulares WHERE id=?", (titular_id,))
    if not t:
        raise DatosNoValidos("Titular no encontrado")
    conn.execute(
        "UPDATE titulares SET nombre=?, perfil_laboral=?, dni_nie=?, email=? WHERE id=?",
        (nombre.strip()[:200], perfil_laboral, (dni_nie or "").strip().upper().replace("-", "")[:20],
         _emails(email).split(",")[0].strip(), titular_id),
    )
    registrar(conn, "titular_modificado", f"titular #{titular_id}", t["expediente_id"])


def emails_de_expediente(conn: sqlite3.Connection, exp: sqlite3.Row) -> set[str]:
    emails = {e.strip() for e in exp["emails_contacto"].split(",") if e.strip()}
    emails |= {t["email"] for t in titulares(conn, exp["id"]) if t["email"]}
    return emails
