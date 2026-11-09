"""Evaluación del checklist documental y comprobaciones de coherencia de un expediente.

Solo se marca como pendiente lo que un requisito activo y aplicable exige
(o lo que la usuaria ha marcado como obligatorio en el expediente).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..catalogo import CAMPOS, etiqueta_campo, etiqueta_tipo_doc
from ..datos import agrupar
from ..db import todos, uno
from ..documentos.texto import normalizar
from ..extraccion.base import buscar_documentos_identidad
from .requisitos import aplica

EQUIVALENTES = {"dni": {"dni", "nie"}, "nie": {"dni", "nie"}}
ESTADOS_NO_VALIDOS = ("descartado", "duplicado", "en_bandeja_incidencias")

ETIQUETAS_ESTADO = {
    "ok": "Correcto",
    "pendiente": "Pendiente",
    "antiguo": "Antiguo (fuera de vigencia)",
    "incompleto": "Incompleto",
    "caducado": "Caducado",
    "revisar_fecha": "Fecha no determinada",
    "incoherente": "Incoherente",
}


@dataclass
class Item:
    requisito: dict
    titular: dict | None
    estado: str
    detalle: str
    documentos: list[dict] = field(default_factory=list)
    modo: str = "auto"
    incoherencias: list[dict] = field(default_factory=list)

    @property
    def etiqueta_estado(self) -> str:
        return ETIQUETAS_ESTADO.get(self.estado, self.estado)


@dataclass
class Evaluacion:
    items: list[Item]
    excluidos: list[dict]
    duplicados: list[dict]
    sin_titular: list[dict]
    sin_clasificar: list[dict]
    con_incidencia: list[dict]
    incoherencias: list[dict]

    @property
    def pendientes(self) -> list[Item]:
        return [i for i in self.items if i.estado != "ok"]


def _tokens(nombre: str) -> set[str]:
    return {t for t in normalizar(nombre).replace(",", " ").split() if len(t) > 2 and t not in ("DEL", "LOS", "LAS")}


def comprobar_coherencia(conn: sqlite3.Connection, exp_id: int, hoy: date | None = None) -> list[dict]:
    hoy = hoy or date.today()
    res: list[dict] = []
    tits = {t["id"]: t for t in todos(conn, "SELECT * FROM titulares WHERE expediente_id=?", (exp_id,))}
    grupos = agrupar(conn, exp_id)

    def add(nivel, mensaje, titular_id=None, documento_id=None):
        res.append({"nivel": nivel, "mensaje": mensaje, "titular_id": titular_id, "documento_id": documento_id})

    for (tid, clave), filas in grupos.items():
        t = tits.get(tid)
        if clave in ("nombre_en_documento", "nombre_completo") and t:
            esperado = _tokens(t["nombre"])
            for f in filas:
                hallado = _tokens(f["valor"])
                comunes = esperado & hallado
                if hallado and len(comunes) < min(2, len(esperado), len(hallado)):
                    add("error", f"El nombre en el documento #{f['documento_id']} («{f['valor']}») no coincide con el titular «{t['nombre']}»",
                        tid, f["documento_id"])
        if clave == "dni_nie":
            valores = {f["valor"] for f in filas}
            if len(valores) > 1:
                add("error", f"Constan varios DNI/NIE distintos para {t['nombre'] if t else 'el expediente'}: {', '.join(sorted(valores))}", tid)
            if t and t["dni_nie"]:
                for f in filas:
                    if f["valor"] != t["dni_nie"]:
                        add("error", f"El DNI/NIE del documento #{f['documento_id']} ({f['valor']}) no coincide con el registrado ({t['dni_nie']})",
                            tid, f["documento_id"])
        if clave == "validez_documento":
            for f in filas:
                try:
                    if date.fromisoformat(f["valor"]) < hoy:
                        add("error", f"Documento de identidad caducado el {f['valor']} (documento #{f['documento_id']})", tid, f["documento_id"])
                except ValueError:
                    pass
        if clave == "nomina_liquido" and len(filas) >= 2:
            vals = [Decimal(f["valor"]) for f in filas]
            if min(vals) > 0 and max(vals) / min(vals) > Decimal("1.30"):
                add("aviso", f"Los líquidos de nómina varían más de un 30 % ({min(vals)} – {max(vals)} €): revisar pagas extra, variables o bajas", tid)
        if clave in CAMPOS and not CAMPOS[clave][3] and clave not in ("dni_nie", "nombre_completo"):
            distintos = {f["valor"] for f in filas}
            if len(distintos) > 1:
                quien = tits[tid]["nombre"] if tid in tits else "el expediente"
                add("aviso", f"Valores distintos de «{etiqueta_campo(clave)}» para {quien}: {', '.join(sorted(distintos))}", tid)

    # Documento asignado a un titular pero que contiene el DNI de otro titular del expediente
    if len(tits) > 1:
        for d in todos(conn, "SELECT id, titular_id FROM documentos WHERE expediente_id=? AND titular_id IS NOT NULL "
                             "AND estado NOT IN ('descartado','duplicado')", (exp_id,)):
            texto = "\n".join(r["texto"] for r in todos(conn, "SELECT texto FROM paginas_texto WHERE documento_id=?", (d["id"],)))
            ids = set(buscar_documentos_identidad(texto))
            propio = tits[d["titular_id"]]["dni_nie"] if d["titular_id"] in tits else ""
            ajenos = [t for t in tits.values() if t["id"] != d["titular_id"] and t["dni_nie"] and t["dni_nie"] in ids]
            if ajenos and (not propio or propio not in ids):
                add("error", f"El documento #{d['id']} está asignado a {tits[d['titular_id']]['nombre']} pero contiene el DNI/NIE de "
                             f"{', '.join(t['nombre'] for t in ajenos)}", d["titular_id"], d["id"])
    return res


def evaluar(conn: sqlite3.Connection, exp_id: int, hoy: date | None = None) -> Evaluacion:
    hoy = hoy or date.today()
    exp = uno(conn, "SELECT * FROM expedientes WHERE id=?", (exp_id,))
    tits = [dict(t) for t in todos(conn, "SELECT * FROM titulares WHERE expediente_id=? ORDER BY orden, id", (exp_id,))]
    docs = [dict(d) for d in todos(conn, "SELECT * FROM documentos WHERE expediente_id=? ORDER BY id", (exp_id,))]
    modos = {r["requisito_id"]: r["modo"] for r in todos(conn, "SELECT * FROM requisitos_expediente WHERE expediente_id=?", (exp_id,))}
    reqs = [dict(r) for r in todos(conn, "SELECT * FROM requisitos ORDER BY id")]
    grupos = agrupar(conn, exp_id)
    incoherencias = comprobar_coherencia(conn, exp_id, hoy)

    items: list[Item] = []
    excluidos: list[dict] = []
    for req in reqs:
        modo = modos.get(req["id"], "auto")
        if modo == "no_aplica":
            excluidos.append(req)
            continue
        if modo == "auto" and not req["activo"]:
            continue
        objetivos = tits if req["ambito"] == "titular" else [None]
        if req["ambito"] == "titular" and not tits:
            items.append(Item(req, None, "pendiente", "Añade los titulares del expediente para evaluar este requisito", modo=modo))
            continue
        for t in objetivos:
            if modo == "auto" and not aplica(req, exp["tipo_operacion"], t["perfil_laboral"] if t else None):
                continue
            items.append(_evaluar_item(req, t, docs, grupos, incoherencias, modo, hoy))

    tipos_titular = {r["tipo_documento"] for r in reqs if r["ambito"] == "titular"} | {"dni", "nie"}
    return Evaluacion(
        items=items,
        excluidos=excluidos,
        duplicados=[d for d in docs if d["estado"] == "duplicado"],
        sin_titular=[d for d in docs if len(tits) > 1 and d["titular_id"] is None and d["tipo_documento"] in tipos_titular
                     and d["estado"] not in ESTADOS_NO_VALIDOS],
        sin_clasificar=[d for d in docs if d["tipo_documento"] == "desconocido" and d["estado"] not in ESTADOS_NO_VALIDOS],
        con_incidencia=[d for d in docs if d["estado"] == "incidencia"],
        incoherencias=incoherencias,
    )


def _evaluar_item(req, t, docs, grupos, incoherencias, modo, hoy) -> Item:
    tipos = EQUIVALENTES.get(req["tipo_documento"], {req["tipo_documento"]})
    candidatos = [
        d for d in docs
        if d["tipo_documento"] in tipos and d["estado"] not in ESTADOS_NO_VALIDOS
        and (t is None or d["titular_id"] == t["id"])
    ]
    validos = [d for d in candidatos if d["estado"] != "incidencia"]
    vig = req["vigencia_dias"]
    minimo = req["minimo"]
    nombre = etiqueta_tipo_doc(req["tipo_documento"])

    def antiguedad(d):
        try:
            return (hoy - date.fromisoformat(d["fecha_documento"])).days if d["fecha_documento"] else None
        except ValueError:
            return None

    vigentes = [d for d in validos if vig is None or (antiguedad(d) is not None and antiguedad(d) <= vig)]
    sin_fecha = [d for d in validos if vig is not None and antiguedad(d) is None]
    relacionados = {d["id"] for d in candidatos}
    inc = [i for i in incoherencias if i["documento_id"] in relacionados
           or (req["tipo_documento"] in ("dni", "nie") and t and i["titular_id"] == t["id"] and "DNI" in i["mensaje"])]

    caducado = False
    if req["controla_caducidad"] and t is not None:
        for f in grupos.get((t["id"], "validez_documento"), []):
            if f["documento_id"] in relacionados:
                try:
                    caducado = caducado or date.fromisoformat(f["valor"]) < hoy
                except ValueError:
                    pass

    if not candidatos:
        estado, detalle = "pendiente", f"No se ha recibido {nombre.lower()}" + (f" (se requieren {minimo})" if minimo > 1 else "")
    elif not validos:
        estado, detalle = "incompleto", "Los documentos recibidos tienen incidencias (ilegibles o formato no admitido)"
    elif caducado:
        estado, detalle = "caducado", "El documento de identidad está caducado"
    elif len(vigentes) >= minimo and not any(d["marcado_incompleto"] for d in vigentes):
        estado, detalle = "ok", f"{len(vigentes)} documento(s) válido(s)"
    elif any(d["marcado_incompleto"] for d in vigentes):
        estado, detalle = "incompleto", "Algún documento está marcado como incompleto"
    elif sin_fecha and len(vigentes) + len(sin_fecha) >= minimo:
        estado, detalle = "revisar_fecha", f"{len(sin_fecha)} documento(s) sin fecha identificada: indica la fecha para comprobar la vigencia"
    elif not vigentes and not sin_fecha:
        estado, detalle = "antiguo", f"Ningún documento tiene menos de {vig} días"
    else:
        estado, detalle = "incompleto", f"{len(vigentes)} de {minimo} documento(s) vigentes"
    if estado == "ok" and any(i["nivel"] == "error" for i in inc):
        estado, detalle = "incoherente", "Hay incoherencias en los datos de estos documentos"
    return Item(req, t, estado, detalle, candidatos, modo, inc)
