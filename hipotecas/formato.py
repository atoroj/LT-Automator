"""Formato español de importes, porcentajes y fechas."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


def euros(x) -> str:
    if x is None or x == "":
        return "—"
    d = Decimal(str(x)).quantize(Decimal("0.01"))
    entero, dec = f"{abs(d):,.2f}".split(".")
    return f"{'-' if d < 0 else ''}{entero.replace(',', '.')},{dec} €"


def pct(x) -> str:
    if x is None or x == "":
        return "—"
    return f"{Decimal(str(x)).quantize(Decimal('0.01'))}".replace(".", ",") + " %"


def fecha(x) -> str:
    if not x:
        return "—"
    try:
        if isinstance(x, (date, datetime)):
            return x.strftime("%d/%m/%Y")
        return datetime.fromisoformat(str(x)).strftime("%d/%m/%Y")
    except ValueError:
        return str(x)


def fecha_hora(x) -> str:
    if not x:
        return "—"
    try:
        return datetime.fromisoformat(str(x)).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(x)


def valor_campo(tipo: str, valor) -> str:
    if valor in (None, ""):
        return "—"
    if tipo == "importe":
        return euros(valor)
    if tipo == "porcentaje":
        return pct(valor)
    if tipo == "fecha":
        return fecha(valor)
    return str(valor)
