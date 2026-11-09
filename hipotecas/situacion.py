"""Análisis del expediente: reúne datos con su procedencia y ejecuta los cálculos deterministas."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal

from .catalogo import CAMPOS, etiqueta_tipo_doc
from .checklist.evaluador import evaluar
from .config import Config
from .datos import agrupar
from .db import todos, uno
from .finanzas import EntradaSimulacion, redondear, simular


@dataclass
class Valor:
    valor: str | None
    estado: str          # acreditado | declarado | calculado | pendiente_confirmar | configuracion | pendiente
    fuente: str
    campo_id: int | None = None
    basado_en_pendientes: bool = False

    @property
    def decimal(self) -> Decimal | None:
        try:
            return Decimal(self.valor) if self.valor not in (None, "") else None
        except Exception:  # noqa: BLE001
            return None


def describir_fuente(r: sqlite3.Row) -> str:
    if r["documento_id"]:
        pag = f", pág. {r['pagina']}" if r["pagina"] else ""
        return f"{etiqueta_tipo_doc(r['doc_tipo'] or '')} (doc. #{r['documento_id']}{pag})"
    return "Introducido manualmente" + (f": {r['nota']}" if r["nota"] else "")


def _desde_fila(r: sqlite3.Row) -> Valor:
    return Valor(r["valor"], r["estado"], describir_fuente(r), r["id"], r["estado"] == "pendiente_confirmar")


def resolver(grupos, titular_id, clave) -> Valor | None:
    filas = grupos.get((titular_id, clave))
    return _desde_fila(filas[0]) if filas else None


def _sin_dato(clave: str) -> Valor:
    return Valor(None, "pendiente", "Sin dato")


def _ingresos_titular(grupos, tid, n_nominas: int) -> tuple[Valor, list[str]]:
    notas: list[str] = []
    directo = resolver(grupos, tid, "ingresos_netos_mensuales")
    if directo and directo.estado == "acreditado":
        return directo, notas
    nominas = grupos.get((tid, "nomina_liquido"), [])
    if nominas:
        recientes = sorted(nominas, key=lambda r: (r["doc_fecha"] or "", r["id"]), reverse=True)[:n_nominas]
        media = sum(Decimal(r["valor"]) for r in recientes) / len(recientes)
        pagas = resolver(grupos, tid, "pagas_anuales")
        fuente = f"Media de {len(recientes)} nómina(s) (docs. {', '.join('#' + str(r['documento_id']) for r in recientes)})"
        pendientes = any(r["estado"] == "pendiente_confirmar" for r in recientes)
        if pagas and pagas.decimal and pagas.decimal > 12:
            media = media * pagas.decimal / 12
            fuente += f" × {pagas.valor} pagas / 12"
            pendientes = pendientes or pagas.basado_en_pendientes
        else:
            notas.append("No consta el número de pagas anuales: no se han prorrateado pagas extra")
        return Valor(str(redondear(media)), "calculado", fuente, None, pendientes), notas
    if directo:
        return directo, notas
    return _sin_dato("ingresos_netos_mensuales"), notas


def analizar(conn: sqlite3.Connection, cfg: Config, exp_id: int) -> dict:
    exp = uno(conn, "SELECT * FROM expedientes WHERE id=?", (exp_id,))
    tits = todos(conn, "SELECT * FROM titulares WHERE expediente_id=? ORDER BY orden, id", (exp_id,))
    grupos = agrupar(conn, exp_id)
    c = cfg["calculos"]
    notas: list[str] = []

    titulares = []
    total_ingresos = Decimal(0)
    ingresos_conocidos = False
    ingresos_pendientes = False
    deudas_total = Decimal(0)
    deudas_pendientes = False
    for t in tits:
        datos = {}
        for clave in ("nombre_completo", "dni_nie", "fecha_nacimiento", "validez_documento", "empresa", "cif_empresa",
                      "tipo_contrato", "fecha_antiguedad", "fecha_inicio_contrato", "pagas_anuales", "ejercicio_irpf",
                      "rendimiento_neto_trabajo", "base_imponible_general", "dias_cotizados"):
            v = resolver(grupos, t["id"], clave)
            if v:
                datos[clave] = v
        ingresos, n = _ingresos_titular(grupos, t["id"], int(c.get("nominas_para_media", 3)))
        notas += [f"{t['nombre']}: {x}" for x in n]
        extras = [v for v in (resolver(grupos, t["id"], "otros_ingresos_mensuales"), resolver(grupos, t["id"], "pension_mensual")) if v]
        for v in [ingresos, *extras]:
            if v.decimal is not None:
                total_ingresos += v.decimal
                ingresos_conocidos = True
                ingresos_pendientes = ingresos_pendientes or v.basado_en_pendientes
        deudas = []
        for r in grupos.get((t["id"], "cuota_prestamo"), []):
            saldo = next((s for s in grupos.get((t["id"], "saldo_prestamo"), []) if s["documento_id"] == r["documento_id"]), None)
            entidad = next((s for s in grupos.get((t["id"], "entidad_prestamo"), []) if s["documento_id"] == r["documento_id"]), None)
            deudas.append({"cuota": _desde_fila(r), "saldo": _desde_fila(saldo) if saldo else None,
                           "entidad": entidad["valor"] if entidad else ""})
            deudas_total += Decimal(r["valor"])
            deudas_pendientes = deudas_pendientes or r["estado"] == "pendiente_confirmar"
        titulares.append({"titular": dict(t), "datos": datos, "ingresos": ingresos, "otros_ingresos": extras, "deudas": deudas})

    def op(clave, defecto_cfg=None):
        v = resolver(grupos, None, clave)
        if v:
            return v
        if defecto_cfg is not None:
            return Valor(str(defecto_cfg), "configuracion", "Valor por defecto de la configuración")
        return _sin_dato(clave)

    operacion = {
        "precio_compra": op("precio_compra"),
        "valor_tasacion": op("valor_tasacion"),
        "importe_solicitado": op("importe_solicitado"),
        "plazo_anios": op("plazo_anios", c["plazo_anios"]),
        "tipo_interes_anual": op("tipo_interes_anual", c["tipo_interes_anual"]),
        "ahorros_aportados": op("ahorros_aportados"),
        "importe_arras": op("importe_arras"),
        "porcentaje_impuestos_compra": op("porcentaje_impuestos_compra", c["porcentaje_impuestos_compra"]),
        "gastos_fijos_estimados": op("gastos_fijos_estimados", c["gastos_fijos_estimados"]),
        "referencia_catastral": op("referencia_catastral"),
    }

    entrada = EntradaSimulacion(
        ingresos_netos_mensuales=redondear(total_ingresos) if ingresos_conocidos else None,
        deudas_mensuales=redondear(deudas_total),
        precio_compra=operacion["precio_compra"].decimal,
        valor_tasacion=operacion["valor_tasacion"].decimal,
        importe_solicitado=operacion["importe_solicitado"].decimal,
        plazo_anios=int(operacion["plazo_anios"].decimal or c["plazo_anios"]),
        tipo_interes_anual=operacion["tipo_interes_anual"].decimal,
        ahorros_aportados=operacion["ahorros_aportados"].decimal,
        porcentaje_impuestos=operacion["porcentaje_impuestos_compra"].decimal,
        gastos_fijos=operacion["gastos_fijos_estimados"].decimal,
        ratio_maximo=Decimal(str(c["ratio_endeudamiento_maximo"])),
        ltv_maximo=Decimal(str(c["ltv_maximo"])),
        financiacion_maxima=Decimal(str(c["financiacion_maxima_precio"])),
        incremento_estres=Decimal(str(c["incremento_estres_puntos"])),
    )
    resultado = simular(entrada)
    entradas_pendientes = ingresos_pendientes or deudas_pendientes or any(
        v.estado == "pendiente_confirmar" for v in operacion.values()
    )
    if entradas_pendientes:
        notas.append("Algunos cálculos se basan en datos extraídos automáticamente aún pendientes de confirmar")
    if not any(t["deudas"] for t in titulares):
        notas.append("No constan préstamos vigentes acreditados ni declarados (endeudamiento previo considerado 0)")

    pendientes_confirmar = [
        r for filas in grupos.values() for r in filas if r["estado"] == "pendiente_confirmar"
    ]
    return {
        "expediente": dict(exp),
        "titulares": titulares,
        "operacion": operacion,
        "ingresos_totales": entrada.ingresos_netos_mensuales,
        "deudas_totales": entrada.deudas_mensuales,
        "entrada": entrada,
        "resultado": resultado,
        "notas": notas,
        "entradas_pendientes": entradas_pendientes,
        "pendientes_confirmar": pendientes_confirmar,
        "checklist": evaluar(conn, exp_id),
        "campos_catalogo": CAMPOS,
    }
