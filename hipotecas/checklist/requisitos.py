"""Requisitos documentales configurables (tabla `requisitos`, editable desde la interfaz)."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..catalogo import PERFILES_LABORALES, TIPOS_DOCUMENTO, TIPOS_OPERACION

RUTA_DEFECTO = Path(__file__).with_name("requisitos_por_defecto.json")
COLUMNAS = ("codigo", "tipo_documento", "descripcion", "ambito", "operaciones", "perfiles",
            "minimo", "vigencia_dias", "controla_caducidad", "activo")


class RequisitoNoValido(ValueError):
    pass


def _lista(valor: str, permitidos: dict) -> str:
    valor = (valor or "").strip()
    if valor in ("", "*"):
        return "*"
    items = [v.strip() for v in valor.split(",") if v.strip()]
    malos = [v for v in items if v not in permitidos]
    if malos:
        raise RequisitoNoValido(f"Valores no reconocidos: {', '.join(malos)}")
    return ",".join(items)


def validar(d: dict) -> dict:
    codigo = (d.get("codigo") or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_]{2,40}", codigo):
        raise RequisitoNoValido("Código: 2-40 caracteres A-Z, 0-9 o _")
    if d.get("tipo_documento") not in TIPOS_DOCUMENTO or d.get("tipo_documento") == "desconocido":
        raise RequisitoNoValido("Tipo de documento no válido")
    if d.get("ambito") not in ("titular", "expediente"):
        raise RequisitoNoValido("Ámbito debe ser 'titular' o 'expediente'")
    try:
        minimo = int(d.get("minimo") or 1)
        vig = d.get("vigencia_dias")
        vig = int(vig) if vig not in (None, "", "None") else None
    except (TypeError, ValueError) as e:
        raise RequisitoNoValido("Mínimo y vigencia deben ser números enteros") from e
    if minimo < 1 or (vig is not None and vig < 1):
        raise RequisitoNoValido("Mínimo y vigencia deben ser positivos")
    return {
        "codigo": codigo,
        "tipo_documento": d["tipo_documento"],
        "descripcion": (d.get("descripcion") or "").strip()[:200] or TIPOS_DOCUMENTO[d["tipo_documento"]][0],
        "ambito": d["ambito"],
        "operaciones": _lista(d.get("operaciones", "*"), TIPOS_OPERACION),
        "perfiles": _lista(d.get("perfiles", "*"), PERFILES_LABORALES),
        "minimo": minimo,
        "vigencia_dias": vig,
        "controla_caducidad": 1 if d.get("controla_caducidad") in (1, "1", True, "on") else 0,
        "activo": 1 if d.get("activo") in (1, "1", True, "on") else 0,
    }


def sembrar_requisitos_por_defecto(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM requisitos").fetchone()[0]:
        return
    for d in json.loads(RUTA_DEFECTO.read_text(encoding="utf-8")):
        guardar(conn, validar(d))


def guardar(conn: sqlite3.Connection, d: dict, requisito_id: int | None = None) -> int:
    d = validar(d)
    valores = [d[c] for c in COLUMNAS]
    try:
        if requisito_id:
            conn.execute(f"UPDATE requisitos SET {', '.join(f'{c}=?' for c in COLUMNAS)} WHERE id=?", (*valores, requisito_id))
            return requisito_id
        cur = conn.execute(f"INSERT INTO requisitos({', '.join(COLUMNAS)}) VALUES ({','.join('?' * len(COLUMNAS))})", valores)
        return cur.lastrowid
    except sqlite3.IntegrityError as e:
        raise RequisitoNoValido("Ya existe un requisito con ese código") from e


def exportar_json(conn: sqlite3.Connection) -> str:
    filas = conn.execute(f"SELECT {', '.join(COLUMNAS)} FROM requisitos ORDER BY id").fetchall()
    return json.dumps([dict(r) for r in filas], ensure_ascii=False, indent=2)


def aplica(req: sqlite3.Row, tipo_operacion: str, perfil: str | None) -> bool:
    ops = req["operaciones"]
    if ops != "*" and tipo_operacion not in ops.split(","):
        return False
    if req["ambito"] == "titular":
        perfiles = req["perfiles"]
        if perfiles != "*" and (perfil or "") not in perfiles.split(","):
            return False
    return True


def fijar_modo_expediente(conn: sqlite3.Connection, exp_id: int, requisito_id: int, modo: str) -> None:
    """modo: 'auto' (según reglas), 'obligatorio' (forzar) o 'no_aplica' (excluir)."""
    if modo == "auto":
        conn.execute("DELETE FROM requisitos_expediente WHERE expediente_id=? AND requisito_id=?", (exp_id, requisito_id))
    elif modo in ("obligatorio", "no_aplica"):
        conn.execute(
            "INSERT INTO requisitos_expediente(expediente_id, requisito_id, modo) VALUES (?,?,?) "
            "ON CONFLICT(expediente_id, requisito_id) DO UPDATE SET modo=excluded.modo",
            (exp_id, requisito_id, modo),
        )
    else:
        raise RequisitoNoValido("Modo no válido")
