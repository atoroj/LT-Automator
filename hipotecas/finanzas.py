"""Cálculos financieros deterministas (sin IA). Todos los importes en Decimal.

Los resultados son orientativos y no constituyen una decisión de crédito.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, getcontext

getcontext().prec = 28
CENT = Decimal("0.01")


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def redondear(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def cuota_francesa(capital, tipo_anual_pct, plazo_meses: int) -> Decimal:
    """Cuota constante del sistema francés: C·i / (1 − (1+i)^−n), con i = TIN/12."""
    capital, tipo = D(capital), D(tipo_anual_pct)
    if plazo_meses <= 0:
        raise ValueError("El plazo debe ser positivo")
    if capital <= 0:
        return Decimal("0.00")
    i = tipo / Decimal(1200)
    if i == 0:
        return redondear(capital / plazo_meses)
    return redondear(capital * i / (1 - (1 + i) ** -plazo_meses))


def capital_maximo(cuota, tipo_anual_pct, plazo_meses: int) -> Decimal:
    """Capital que corresponde a una cuota dada (inversa de la cuota francesa)."""
    cuota, tipo = D(cuota), D(tipo_anual_pct)
    if cuota <= 0:
        return Decimal("0.00")
    i = tipo / Decimal(1200)
    if i == 0:
        return redondear(cuota * plazo_meses)
    return redondear(cuota * (1 - (1 + i) ** -plazo_meses) / i)


def cuadro_amortizacion(capital, tipo_anual_pct, plazo_meses: int) -> list[dict]:
    """Cuadro completo; la última cuota ajusta el redondeo para dejar el saldo a cero."""
    capital = D(capital)
    cuota = cuota_francesa(capital, tipo_anual_pct, plazo_meses)
    i = D(tipo_anual_pct) / Decimal(1200)
    saldo = capital
    filas = []
    for n in range(1, plazo_meses + 1):
        interes = redondear(saldo * i)
        amort = cuota - interes if n < plazo_meses else saldo
        c = amort + interes
        saldo = saldo - amort
        filas.append({"mes": n, "cuota": c, "interes": interes, "amortizacion": amort, "saldo": saldo})
    return filas


def porcentaje(parte, total) -> Decimal | None:
    parte, total = D(parte), D(total)
    if total <= 0:
        return None
    return redondear(parte / total * 100)


@dataclass
class EntradaSimulacion:
    ingresos_netos_mensuales: Decimal | None
    deudas_mensuales: Decimal
    precio_compra: Decimal | None
    valor_tasacion: Decimal | None
    importe_solicitado: Decimal | None
    plazo_anios: int
    tipo_interes_anual: Decimal
    ahorros_aportados: Decimal | None
    porcentaje_impuestos: Decimal
    gastos_fijos: Decimal
    ratio_maximo: Decimal
    ltv_maximo: Decimal
    financiacion_maxima: Decimal
    incremento_estres: Decimal


@dataclass
class ResultadoSimulacion:
    cuota: Decimal | None = None
    total_intereses: Decimal | None = None
    total_pagado: Decimal | None = None
    cuota_estres: Decimal | None = None
    tipo_estres: Decimal | None = None
    endeudamiento_actual: Decimal | None = None
    endeudamiento_con_hipoteca: Decimal | None = None
    endeudamiento_estres: Decimal | None = None
    ltv: Decimal | None = None
    base_ltv: str = ""
    financiacion_sobre_precio: Decimal | None = None
    impuestos: Decimal | None = None
    gastos_totales: Decimal | None = None
    coste_total_compra: Decimal | None = None
    aportacion_necesaria: Decimal | None = None
    diferencia_ahorros: Decimal | None = None
    cuota_maxima_por_ratio: Decimal | None = None
    importe_maximo_por_ratio: Decimal | None = None
    importe_maximo_por_ltv: Decimal | None = None
    alertas: list[str] = field(default_factory=list)
    faltan: list[str] = field(default_factory=list)


def simular(e: EntradaSimulacion) -> ResultadoSimulacion:
    r = ResultadoSimulacion()
    meses = int(e.plazo_anios) * 12

    if e.importe_solicitado:
        r.cuota = cuota_francesa(e.importe_solicitado, e.tipo_interes_anual, meses)
        r.total_pagado = redondear(r.cuota * meses)
        r.total_intereses = redondear(r.total_pagado - e.importe_solicitado)
        r.tipo_estres = e.tipo_interes_anual + e.incremento_estres
        r.cuota_estres = cuota_francesa(e.importe_solicitado, r.tipo_estres, meses)
    else:
        r.faltan.append("importe solicitado")

    if e.ingresos_netos_mensuales and e.ingresos_netos_mensuales > 0:
        ing = e.ingresos_netos_mensuales
        r.endeudamiento_actual = porcentaje(e.deudas_mensuales, ing)
        if r.cuota is not None:
            r.endeudamiento_con_hipoteca = porcentaje(e.deudas_mensuales + r.cuota, ing)
            r.endeudamiento_estres = porcentaje(e.deudas_mensuales + r.cuota_estres, ing)
            if r.endeudamiento_con_hipoteca > e.ratio_maximo:
                r.alertas.append(
                    f"El endeudamiento con la nueva cuota ({r.endeudamiento_con_hipoteca} %) supera el umbral configurado ({e.ratio_maximo} %)"
                )
        r.cuota_maxima_por_ratio = redondear(max(Decimal(0), ing * e.ratio_maximo / 100 - e.deudas_mensuales))
        r.importe_maximo_por_ratio = capital_maximo(r.cuota_maxima_por_ratio, e.tipo_interes_anual, meses)
    else:
        r.faltan.append("ingresos netos mensuales")

    valores = [v for v in (e.precio_compra, e.valor_tasacion) if v and v > 0]
    if valores:
        base = min(valores)
        r.base_ltv = (
            "menor entre precio y tasación" if len(valores) == 2
            else ("precio de compra (sin tasación)" if e.precio_compra else "valor de tasación")
        )
        r.importe_maximo_por_ltv = redondear(base * e.ltv_maximo / 100)
        if e.importe_solicitado:
            r.ltv = porcentaje(e.importe_solicitado, base)
            if r.ltv > e.ltv_maximo:
                r.alertas.append(f"El LTV ({r.ltv} %) supera el máximo configurado ({e.ltv_maximo} %)")
    else:
        r.faltan.append("precio de compra o valor de tasación")

    if e.precio_compra and e.precio_compra > 0:
        r.impuestos = redondear(e.precio_compra * e.porcentaje_impuestos / 100)
        r.gastos_totales = redondear(r.impuestos + e.gastos_fijos)
        r.coste_total_compra = redondear(e.precio_compra + r.gastos_totales)
        if e.importe_solicitado:
            r.financiacion_sobre_precio = porcentaje(e.importe_solicitado, e.precio_compra)
            if r.financiacion_sobre_precio > e.financiacion_maxima:
                r.alertas.append(
                    f"La financiación sobre precio ({r.financiacion_sobre_precio} %) supera el máximo configurado ({e.financiacion_maxima} %)"
                )
            r.aportacion_necesaria = redondear(r.coste_total_compra - e.importe_solicitado)
            if e.ahorros_aportados is not None:
                r.diferencia_ahorros = redondear(e.ahorros_aportados - r.aportacion_necesaria)
                if r.diferencia_ahorros < 0:
                    r.alertas.append(f"Los ahorros declarados/acreditados no cubren la aportación necesaria (faltan {-r.diferencia_ahorros} €)")
            else:
                r.faltan.append("ahorros aportados")
    return r
