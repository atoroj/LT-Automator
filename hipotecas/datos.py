"""Datos extraídos o introducidos, con trazabilidad y estado de acreditación."""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from .actividad import registrar
from .catalogo import CAMPOS, ESTADOS_CAMPO
from .db import ahora, todos, transaccion, uno
from .extraccion.base import Candidato, normalizar_valor

PRIORIDAD = {"acreditado": 0, "declarado": 1, "calculado": 2, "pendiente_confirmar": 3}


class CampoNoValido(ValueError):
    pass


def guardar_candidatos(
    conn: sqlite3.Connection, exp_id: int, doc_id: int, titular_id: int | None, candidatos: list[Candidato]
) -> int:
    """Sustituye los datos no revisados de este documento por los nuevos candidatos.

    Los datos ya confirmados, corregidos o descartados por la usuaria se conservan.
    """
    with transaccion(conn):
        conn.execute(
            "DELETE FROM campos WHERE documento_id=? AND estado='pendiente_confirmar' AND origen LIKE 'extraccion%'",
            (doc_id,),
        )
        ya_revisados = {
            (r["clave"], r["valor"])
            for r in todos(conn, "SELECT clave, valor FROM campos WHERE documento_id=?", (doc_id,))
        }
        n = 0
        ts = ahora()
        for c in candidatos:
            if c.clave not in CAMPOS or (c.clave, c.valor) in ya_revisados:
                continue
            ambito = CAMPOS[c.clave][2]
            conn.execute(
                "INSERT INTO campos(expediente_id, titular_id, clave, valor, estado, origen, documento_id, pagina, evidencia, creado, actualizado) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (exp_id, titular_id if ambito == "titular" else None, c.clave, c.valor, "pendiente_confirmar",
                 c.origen, doc_id, c.pagina, c.evidencia, ts, ts),
            )
            n += 1
    return n


def anadir_manual(
    conn: sqlite3.Connection,
    exp_id: int,
    clave: str,
    valor: str,
    estado: str,
    titular_id: int | None = None,
    documento_id: int | None = None,
    pagina: int | None = None,
    nota: str = "",
) -> int:
    if clave not in CAMPOS:
        raise CampoNoValido("Campo desconocido")
    if estado not in ("acreditado", "declarado"):
        raise CampoNoValido("Un dato manual solo puede ser 'acreditado' o 'declarado'")
    if estado == "acreditado" and not documento_id:
        raise CampoNoValido("Para marcar un dato como acreditado indica el documento que lo acredita")
    _, tipo, ambito, _ = CAMPOS[clave]
    if ambito == "titular" and not titular_id:
        raise CampoNoValido("Este dato pertenece a un titular: selecciona el titular")
    norm = normalizar_valor(tipo, valor)
    if norm is None:
        raise CampoNoValido(f"Valor no válido para un campo de tipo {tipo}")
    if documento_id:
        d = uno(conn, "SELECT expediente_id FROM documentos WHERE id=?", (documento_id,))
        if not d or d["expediente_id"] != exp_id:
            raise CampoNoValido("El documento no pertenece a este expediente")
    ts = ahora()
    cur = conn.execute(
        "INSERT INTO campos(expediente_id, titular_id, clave, valor, estado, origen, documento_id, pagina, nota, creado, actualizado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (exp_id, titular_id if ambito == "titular" else None, clave, norm, estado, "manual", documento_id, pagina, nota[:500], ts, ts),
    )
    registrar(conn, "dato_anadido", f"{clave} ({estado})", exp_id, documento_id)
    return cur.lastrowid


def _campo(conn, campo_id: int) -> sqlite3.Row:
    c = uno(conn, "SELECT * FROM campos WHERE id=?", (campo_id,))
    if not c:
        raise CampoNoValido("Dato no encontrado")
    return c


def confirmar(conn: sqlite3.Connection, campo_id: int) -> None:
    c = _campo(conn, campo_id)
    nuevo = "acreditado" if c["documento_id"] else "declarado"
    conn.execute("UPDATE campos SET estado=?, actualizado=? WHERE id=?", (nuevo, ahora(), campo_id))
    registrar(conn, "dato_confirmado", f"{c['clave']} -> {nuevo}", c["expediente_id"], c["documento_id"])


def descartar(conn: sqlite3.Connection, campo_id: int) -> None:
    c = _campo(conn, campo_id)
    conn.execute("UPDATE campos SET estado='descartado', actualizado=? WHERE id=?", (ahora(), campo_id))
    registrar(conn, "dato_descartado", c["clave"], c["expediente_id"], c["documento_id"])


def corregir(conn: sqlite3.Connection, campo_id: int, valor: str, nota: str = "") -> None:
    """La usuaria corrige el valor tras revisar el documento: queda acreditado (o declarado si no hay documento)."""
    c = _campo(conn, campo_id)
    norm = normalizar_valor(CAMPOS[c["clave"]][1], valor)
    if norm is None:
        raise CampoNoValido("Valor no válido")
    nuevo = "acreditado" if c["documento_id"] else "declarado"
    conn.execute(
        "UPDATE campos SET valor=?, estado=?, origen='manual_corregido', nota=?, actualizado=? WHERE id=?",
        (norm, nuevo, nota[:500], ahora(), campo_id),
    )
    registrar(conn, "dato_corregido", c["clave"], c["expediente_id"], c["documento_id"])


def campos_expediente(conn: sqlite3.Connection, exp_id: int, incluir_descartados: bool = True) -> list[sqlite3.Row]:
    sql = (
        "SELECT c.*, d.nombre_original AS doc_nombre, d.tipo_documento AS doc_tipo, d.fecha_documento AS doc_fecha "
        "FROM campos c LEFT JOIN documentos d ON d.id=c.documento_id WHERE c.expediente_id=?"
    )
    if not incluir_descartados:
        sql += " AND c.estado!='descartado' AND (d.id IS NULL OR d.estado NOT IN ('descartado','duplicado'))"
    return todos(conn, sql + " ORDER BY c.titular_id, c.clave, c.id", (exp_id,))


def agrupar(conn: sqlite3.Connection, exp_id: int) -> dict[tuple[int | None, str], list[sqlite3.Row]]:
    """{(titular_id|None, clave): [filas válidas ordenadas por prioridad de estado]}"""
    grupos: dict = defaultdict(list)
    for r in campos_expediente(conn, exp_id, incluir_descartados=False):
        grupos[(r["titular_id"], r["clave"])].append(r)
    for k in grupos:
        grupos[k].sort(key=lambda r: (PRIORIDAD.get(r["estado"], 9), -(r["id"])))
    return dict(grupos)


def etiqueta_estado(estado: str) -> str:
    return ESTADOS_CAMPO.get(estado, estado)
