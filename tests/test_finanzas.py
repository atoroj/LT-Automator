from decimal import Decimal

import pytest

from hipotecas.finanzas import EntradaSimulacion, capital_maximo, cuadro_amortizacion, cuota_francesa, simular


def test_cuota_francesa_valor_conocido():
    # 200.000 € a 30 años al 3 %: 843,21 €/mes (valor de referencia estándar)
    assert cuota_francesa(200000, 3, 360) == Decimal("843.21")


def test_cuota_tipo_cero():
    assert cuota_francesa(120000, 0, 240) == Decimal("500.00")


def test_capital_maximo_es_inversa():
    c = cuota_francesa(150000, 2.5, 300)
    assert abs(capital_maximo(c, 2.5, 300) - Decimal(150000)) < Decimal("5")


def test_cuadro_amortizacion_cierra_a_cero():
    filas = cuadro_amortizacion(100000, 3.2, 120)
    assert filas[-1]["saldo"] == 0
    assert sum(f["amortizacion"] for f in filas) == Decimal(100000)


def test_plazo_invalido():
    with pytest.raises(ValueError):
        cuota_francesa(1000, 3, 0)


def _entrada(**kw):
    base = dict(
        ingresos_netos_mensuales=Decimal("3000"), deudas_mensuales=Decimal("200"), precio_compra=Decimal("250000"),
        valor_tasacion=Decimal("260000"), importe_solicitado=Decimal("200000"), plazo_anios=30,
        tipo_interes_anual=Decimal("3"), ahorros_aportados=Decimal("70000"), porcentaje_impuestos=Decimal("8"),
        gastos_fijos=Decimal("2500"), ratio_maximo=Decimal("35"), ltv_maximo=Decimal("80"),
        financiacion_maxima=Decimal("80"), incremento_estres=Decimal("1"),
    )
    base.update(kw)
    return EntradaSimulacion(**base)


def test_simulacion_completa():
    r = simular(_entrada())
    assert r.cuota == Decimal("843.21")
    assert r.endeudamiento_con_hipoteca == Decimal("34.77")   # (200 + 843,21) / 3000
    assert r.ltv == Decimal("80.00")                           # sobre el menor (precio)
    assert r.base_ltv == "menor entre precio y tasación"
    assert r.impuestos == Decimal("20000.00")
    assert r.aportacion_necesaria == Decimal("72500.00")       # 250.000 + 22.500 − 200.000
    assert r.diferencia_ahorros == Decimal("-2500.00")
    assert any("ahorros" in a for a in r.alertas)
    assert r.cuota_estres > r.cuota


def test_simulacion_sin_datos_no_inventa():
    r = simular(_entrada(ingresos_netos_mensuales=None, precio_compra=None, valor_tasacion=None, importe_solicitado=None))
    assert r.cuota is None and r.ltv is None and r.endeudamiento_con_hipoteca is None
    assert set(r.faltan) >= {"importe solicitado", "ingresos netos mensuales", "precio de compra o valor de tasación"}


def test_alerta_endeudamiento():
    r = simular(_entrada(ingresos_netos_mensuales=Decimal("2000")))
    assert any("endeudamiento" in a for a in r.alertas)
