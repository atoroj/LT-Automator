"""Versionado de informes: borradores y aprobación explícita por la usuaria.

- Generar crea siempre una nueva versión en estado 'borrador' (archivos marcados como BORRADOR).
- Aprobar exige una acción explícita (casilla + escribir la referencia del expediente) y genera
  los archivos finales a partir de la MISMA instantánea revisada, como solo lectura.
- Nada se envía a terceros: los archivos quedan en la carpeta de informes del expediente.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .. import almacenamiento as alm
from ..actividad import registrar
from ..config import Config
from ..db import ahora, todos, transaccion, uno
from ..formato import fecha_hora
from ..seguridad import marcar_solo_lectura
from .contenido import construir
from .render import a_docx, a_pdf


class AprobacionNoValida(ValueError):
    pass


def generar_borrador(conn: sqlite3.Connection, cfg: Config, exp_id: int) -> int:
    exp = uno(conn, "SELECT * FROM expedientes WHERE id=?", (exp_id,))
    if not exp:
        raise ValueError("Expediente no encontrado")
    version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM informes WHERE expediente_id=?", (exp_id,)).fetchone()[0]
    contenido = construir(conn, cfg, exp_id, version)
    docx, pdf = a_docx(contenido), a_pdf(contenido)
    carpeta = alm.dir_categoria(cfg, exp["carpeta"], "informes")
    base = f"Informe_{exp['referencia']}_v{version}_BORRADOR"
    ruta_docx = alm.guardar_informe(carpeta, base + ".docx", docx)
    ruta_pdf = alm.guardar_informe(carpeta, base + ".pdf", pdf)
    with transaccion(conn):
        cur = conn.execute(
            "INSERT INTO informes(expediente_id, version, estado, creado, ruta_docx, ruta_pdf, sha256_docx, sha256_pdf, datos_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (exp_id, version, "borrador", ahora(), alm.relativa(cfg, ruta_docx), alm.relativa(cfg, ruta_pdf),
             alm.sha256_bytes(docx), alm.sha256_bytes(pdf), json.dumps(contenido, ensure_ascii=False)),
        )
        registrar(conn, "informe_borrador", f"versión {version}", exp_id)
    return cur.lastrowid


def aprobar(
    conn: sqlite3.Connection, cfg: Config, informe_id: int, confirmacion_revisado: bool, referencia_escrita: str, nota: str = ""
) -> None:
    inf = uno(conn, "SELECT * FROM informes WHERE id=?", (informe_id,))
    if not inf:
        raise AprobacionNoValida("Informe no encontrado")
    exp = uno(conn, "SELECT * FROM expedientes WHERE id=?", (inf["expediente_id"],))
    if inf["estado"] != "borrador":
        raise AprobacionNoValida("Solo se puede aprobar un borrador")
    ultima = conn.execute("SELECT MAX(version) FROM informes WHERE expediente_id=?", (exp["id"],)).fetchone()[0]
    if inf["version"] != ultima:
        raise AprobacionNoValida("Existe una versión más reciente: revisa y aprueba la última")
    if not confirmacion_revisado:
        raise AprobacionNoValida("Debes confirmar que has revisado el informe")
    if (referencia_escrita or "").strip().upper() != exp["referencia"].upper():
        raise AprobacionNoValida("La referencia escrita no coincide con la del expediente")

    contenido = json.loads(inf["datos_json"])
    momento = datetime.now().isoformat(timespec="seconds")
    aprobado = {"fecha_txt": fecha_hora(momento), "nota": nota.strip()[:2000]}
    docx, pdf = a_docx(contenido, aprobado), a_pdf(contenido, aprobado)
    carpeta = alm.dir_categoria(cfg, exp["carpeta"], "informes")
    base = f"Informe_{exp['referencia']}_v{inf['version']}_FINAL"
    ruta_docx = alm.guardar_informe(carpeta, base + ".docx", docx)
    ruta_pdf = alm.guardar_informe(carpeta, base + ".pdf", pdf)
    marcar_solo_lectura(ruta_docx)
    marcar_solo_lectura(ruta_pdf)
    with transaccion(conn):
        conn.execute("UPDATE informes SET estado='sustituido' WHERE expediente_id=? AND estado='aprobado'", (exp["id"],))
        conn.execute(
            "UPDATE informes SET estado='aprobado', aprobado_en=?, nota_aprobacion=?, ruta_docx=?, ruta_pdf=?, "
            "sha256_docx=?, sha256_pdf=? WHERE id=?",
            (momento, aprobado["nota"], alm.relativa(cfg, ruta_docx), alm.relativa(cfg, ruta_pdf),
             alm.sha256_bytes(docx), alm.sha256_bytes(pdf), informe_id),
        )
        registrar(conn, "informe_aprobado", f"versión {inf['version']} aprobada por la usuaria", exp["id"])


def listar(conn: sqlite3.Connection, exp_id: int) -> list[sqlite3.Row]:
    return todos(conn, "SELECT * FROM informes WHERE expediente_id=? ORDER BY version DESC", (exp_id,))
