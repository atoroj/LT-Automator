"""Construye el contenido del informe como estructura serializable (instantánea revisable)."""
from __future__ import annotations

import sqlite3
from datetime import datetime

from ..catalogo import CAMPOS, PERFILES_LABORALES, TIPOS_OPERACION, etiqueta_tipo_doc
from ..config import Config
from ..db import todos
from ..formato import euros, fecha, fecha_hora, pct, valor_campo
from ..situacion import Valor, analizar

ETIQUETA_ESTADO_VALOR = {
    "acreditado": "Acreditado",
    "declarado": "Declarado por el cliente",
    "calculado": "Calculado",
    "pendiente_confirmar": "Pendiente de confirmar",
    "configuracion": "Supuesto de configuración",
    "pendiente": "Pendiente",
}


def _fila(clave: str, v: Valor | None, etiqueta: str | None = None) -> list[str]:
    etiqueta = etiqueta or CAMPOS[clave][0]
    if v is None or v.valor is None:
        return [etiqueta, "—", "Pendiente", "Sin dato"]
    estado = ETIQUETA_ESTADO_VALOR.get(v.estado, v.estado)
    if v.estado == "calculado" and v.basado_en_pendientes:
        estado += " (con datos pendientes de confirmar)"
    return [etiqueta, valor_campo(CAMPOS.get(clave, ("", "texto"))[1], v.valor), estado, v.fuente]


def construir(conn: sqlite3.Connection, cfg: Config, exp_id: int, version: int) -> dict:
    a = analizar(conn, cfg, exp_id)
    exp = a["expediente"]
    r = a["resultado"]
    e = a["entrada"]

    titulares = []
    for t in a["titulares"]:
        tt = t["titular"]
        filas = [_fila(k, v) for k, v in t["datos"].items()]
        filas.append(_fila("ingresos_netos_mensuales", t["ingresos"]))
        for v in t["otros_ingresos"]:
            filas.append(_fila("otros_ingresos_mensuales", v, "Otros ingresos / pensión"))
        deudas = [
            [d["entidad"] or "—", euros(d["cuota"].valor), euros(d["saldo"].valor) if d["saldo"] else "—",
             ETIQUETA_ESTADO_VALOR.get(d["cuota"].estado, d["cuota"].estado), d["cuota"].fuente]
            for d in t["deudas"]
        ]
        titulares.append({
            "nombre": tt["nombre"],
            "dni": tt["dni_nie"] or "—",
            "perfil": PERFILES_LABORALES.get(tt["perfil_laboral"], tt["perfil_laboral"]),
            "filas": filas,
            "deudas": deudas,
        })

    op = a["operacion"]
    operacion = [_fila(k, op[k]) for k in (
        "precio_compra", "valor_tasacion", "importe_solicitado", "plazo_anios", "tipo_interes_anual",
        "ahorros_aportados", "importe_arras", "porcentaje_impuestos_compra", "gastos_fijos_estimados", "referencia_catastral",
    )]

    simulacion = [
        ["Capital solicitado", euros(e.importe_solicitado)],
        ["Plazo", f"{e.plazo_anios} años ({e.plazo_anios * 12} cuotas)"],
        ["Tipo de interés nominal anual", pct(e.tipo_interes_anual)],
        ["Cuota mensual (sistema francés)", euros(r.cuota)],
        ["Total intereses estimados", euros(r.total_intereses)],
        ["Total a pagar", euros(r.total_pagado)],
        [f"Cuota en escenario de estrés ({pct(r.tipo_estres)})", euros(r.cuota_estres)],
    ]
    ratios = [
        ["Ingresos netos mensuales de la unidad familiar", euros(a["ingresos_totales"]), ""],
        ["Cuotas de deudas existentes", euros(a["deudas_totales"]), ""],
        ["Endeudamiento actual", pct(r.endeudamiento_actual), ""],
        ["Endeudamiento con la nueva cuota", pct(r.endeudamiento_con_hipoteca), f"Umbral {pct(e.ratio_maximo)}"],
        ["Endeudamiento en escenario de estrés", pct(r.endeudamiento_estres), f"Umbral {pct(e.ratio_maximo)}"],
        [f"LTV ({r.base_ltv or 'sin base de valoración'})", pct(r.ltv), f"Máximo {pct(e.ltv_maximo)}"],
        ["Financiación sobre precio de compra", pct(r.financiacion_sobre_precio), f"Máximo {pct(e.financiacion_maxima)}"],
        ["Impuestos estimados de la compra", euros(r.impuestos), ""],
        ["Gastos totales estimados (impuestos + gastos fijos)", euros(r.gastos_totales), ""],
        ["Aportación de fondos necesaria", euros(r.aportacion_necesaria), ""],
        ["Ahorros aportados menos aportación necesaria", euros(r.diferencia_ahorros), ""],
        ["Cuota máxima según umbral de endeudamiento", euros(r.cuota_maxima_por_ratio), ""],
        ["Importe máximo según umbral de endeudamiento", euros(r.importe_maximo_por_ratio), ""],
        ["Importe máximo según LTV configurado", euros(r.importe_maximo_por_ltv), ""],
    ]

    ev = a["checklist"]
    pendientes = [
        [i.requisito["descripcion"], i.titular["nombre"] if i.titular else "Operación", i.etiqueta_estado, i.detalle]
        for i in ev.pendientes
    ]
    cuestiones: list[str] = []
    for f in a["pendientes_confirmar"]:
        quien = next((t["titular"]["nombre"] for t in a["titulares"] if t["titular"]["id"] == f["titular_id"]), "Operación")
        cuestiones.append(
            f"Confirmar «{CAMPOS[f['clave']][0]}» = {valor_campo(CAMPOS[f['clave']][1], f['valor'])} ({quien}; "
            f"extraído del doc. #{f['documento_id']}{', pág. ' + str(f['pagina']) if f['pagina'] else ''})"
        )
    cuestiones += [i["mensaje"] for i in ev.incoherencias]
    cuestiones += [f"Documento #{d['id']} ({d['nombre_original']}) sin titular asignado" for d in ev.sin_titular]
    cuestiones += [f"Documento #{d['id']} ({d['nombre_original']}) sin clasificar" for d in ev.sin_clasificar]
    cuestiones += [f"Documento #{d['id']} ({d['nombre_original']}) con incidencia" for d in ev.con_incidencia]
    cuestiones += [f"Falta dato para el cálculo: {x}" for x in r.faltan]

    docs = todos(conn, "SELECT d.*, t.nombre AS titular_nombre FROM documentos d LEFT JOIN titulares t ON t.id=d.titular_id "
                       "WHERE d.expediente_id=? AND d.estado NOT IN ('descartado','duplicado') ORDER BY d.categoria, d.id", (exp_id,))
    aportados = [
        [f"#{d['id']}", etiqueta_tipo_doc(d["tipo_documento"]), d["titular_nombre"] or "—", fecha(d["fecha_documento"]),
         fecha(d["fecha_recepcion"]),
         {"gmail": "Correo", "eml": "Correo (.eml)", "subida_manual": "Subida manual", "carpeta": "Carpeta de entrada"}.get(d["origen"], d["origen"])]
        for d in docs
    ]
    return {
        "meta": {
            "referencia": exp["referencia"],
            "nombre": exp["nombre"],
            "tipo_operacion": TIPOS_OPERACION.get(exp["tipo_operacion"], exp["tipo_operacion"]),
            "generado": datetime.now().isoformat(timespec="seconds"),
            "generado_txt": fecha_hora(datetime.now().isoformat(timespec="seconds")),
            "version": version,
        },
        "titulares": titulares,
        "operacion": operacion,
        "simulacion": simulacion,
        "ratios": ratios,
        "alertas": r.alertas,
        "observaciones": exp["observaciones_informe"] or "",
        "notas_calculo": a["notas"],
        "documentos_pendientes": pendientes,
        "cuestiones": cuestiones,
        "documentos_aportados": aportados,
    }
