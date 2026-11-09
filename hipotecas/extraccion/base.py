"""Interfaz común de proveedores de extracción y utilidades de normalización de valores."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol

from ..documentos.texto import normalizar


@dataclass
class Candidato:
    clave: str          # clave del catálogo o "_fecha_documento"
    valor: str          # valor normalizado (importes "1234.56", fechas ISO)
    pagina: int | None
    evidencia: str      # fragmento corto del texto de origen (se guarda en BD, nunca en logs)
    origen: str = "extraccion_local"


@dataclass
class ResultadoExtraccion:
    candidatos: list[Candidato] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    traza: dict = field(default_factory=dict)  # qué se envió y a quién (para el registro de actividad)
    tipo_documento: str | None = None          # clasificación propuesta (si no venía fijada)
    motivo_tipo: str = ""
    fallo: bool = False                        # el proveedor no pudo completar el análisis


class ProveedorExtraccion(Protocol):
    """Analiza el texto de un documento: lo clasifica (si `tipo_documento` es None) y extrae datos.

    Si `tipo_documento` viene informado (p. ej. fijado por la usuaria), no se reclasifica.
    """
    nombre: str
    externo: bool

    def extraer(self, tipo_documento: str | None, paginas: list[tuple[int, str]]) -> ResultadoExtraccion: ...


# ---------------- Normalización de valores ----------------

_RE_IMPORTE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?|\d+,\d{1,2}|\d+\.\d{2}|\d+)(?![\d,]|\.\d)"
)

MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
    "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
}
_RE_FECHA_NUM = re.compile(r"(?<!\d)(\d{1,2})[/\-. ](\d{1,2})[/\-. ](\d{4})(?!\d)")
_RE_FECHA_TXT = re.compile(r"(?<!\d)(\d{1,2}) DE (" + "|".join(MESES) + r") DE (\d{4})")
_RE_MES_ANIO = re.compile(r"\b(" + "|".join(MESES) + r")\s*(?:DE\s*)?(\d{4})\b")


def parsear_importe(texto: str) -> Decimal | None:
    m = _RE_IMPORTE.search(texto)
    if not m:
        return None
    s = m.group(1)
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def texto_importe(valor: Decimal) -> str:
    return f"{valor.quantize(Decimal('0.01'))}"


def parsear_fecha(texto: str, todas: bool = False):
    """Devuelve la primera fecha válida (o todas) encontradas en el texto normalizado."""
    t = normalizar(texto)
    halladas: list[tuple[int, date]] = []
    for m in _RE_FECHA_NUM.finditer(t):
        try:
            halladas.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            pass
    for m in _RE_FECHA_TXT.finditer(t):
        try:
            halladas.append((m.start(), date(int(m.group(3)), MESES[m.group(2)], int(m.group(1)))))
        except ValueError:
            pass
    halladas.sort(key=lambda x: x[0])
    fechas = [f for _, f in halladas]
    if todas:
        return fechas
    return fechas[0] if fechas else None


def parsear_mes_anio(texto: str) -> date | None:
    """'SEPTIEMBRE 2026' -> último día del mes."""
    m = _RE_MES_ANIO.search(normalizar(texto))
    if not m:
        return None
    anio, mes = int(m.group(2)), MESES[m.group(1)]
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


_LETRAS_DNI = "TRWAGMYFPDXBNJZSQVHLCKE"
_RE_DNI = re.compile(r"\b(\d{8})[- ]?([A-Z])\b")
_RE_NIE = re.compile(r"\b([XYZ])[- ]?(\d{7})[- ]?([A-Z])\b")


def dni_valido(dni: str) -> bool:
    dni = dni.upper().replace("-", "").replace(" ", "")
    m = re.fullmatch(r"(\d{8})([A-Z])", dni)
    if m:
        return _LETRAS_DNI[int(m.group(1)) % 23] == m.group(2)
    m = re.fullmatch(r"([XYZ])(\d{7})([A-Z])", dni)
    if m:
        num = int(str("XYZ".index(m.group(1))) + m.group(2))
        return _LETRAS_DNI[num % 23] == m.group(3)
    return False


def buscar_documentos_identidad(texto: str) -> list[str]:
    t = normalizar(texto)
    res = []
    for m in _RE_DNI.finditer(t):
        v = m.group(1) + m.group(2)
        if dni_valido(v):
            res.append(v)
    for m in _RE_NIE.finditer(t):
        v = m.group(1) + m.group(2) + m.group(3)
        if dni_valido(v):
            res.append(v)
    return list(dict.fromkeys(res))


def normalizar_valor(tipo: str, valor: str) -> str | None:
    """Normaliza un valor según el tipo del catálogo. None si no es interpretable."""
    valor = (valor or "").strip()
    if not valor:
        return None
    if tipo in ("importe", "porcentaje"):
        if re.fullmatch(r"-?\d+(\.\d+)?", valor):
            return texto_importe(Decimal(valor))
        d = parsear_importe(valor)
        return texto_importe(d) if d is not None else None
    if tipo == "fecha":
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
            try:
                return date.fromisoformat(valor).isoformat()
            except ValueError:
                return None
        f = parsear_fecha(valor)
        return f.isoformat() if f else None
    if tipo == "entero":
        m = re.search(r"\d+", valor.replace(".", ""))
        return str(int(m.group(0))) if m else None
    return re.sub(r"\s+", " ", valor)[:200]
