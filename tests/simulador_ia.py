"""Doble de pruebas del cliente de la API de IA (no hace llamadas de red).

Imita `cliente.beta.messages.create(...)` / `cliente.messages.create(...)` y devuelve un JSON con el
mismo esquema que pide el proveedor real, construido a partir de las etiquetas de los documentos
ficticios. Sirve para probar la validación, la caché y el flujo completo sin clave de API.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

CLASIFICACION = [
    ("DOCUMENTO NACIONAL DE IDENTIDAD", "dni"),
    ("RECIBO INDIVIDUAL DE SALARIOS", "nomina"),
    ("CONTRATO DE TRABAJO", "contrato_trabajo"),
    ("INFORME DE VIDA LABORAL", "vida_laboral"),
    ("MODELO 100", "irpf"),
    ("EXTRACTO DE MOVIMIENTOS", "extracto_bancario"),
    ("RECIBO DE PRÉSTAMO", "recibo_prestamo"),
    ("CONTRATO DE ARRAS", "contrato_arras"),
    ("NOTA SIMPLE", "nota_simple"),
]

ETIQUETAS = {
    "DNI": "dni_nie", "FECHA DE NACIMIENTO": "fecha_nacimiento", "VÁLIDO HASTA": "validez_documento",
    "EMPRESA": "empresa", "CIF": "cif_empresa", "TRABAJADORA": "nombre_en_documento", "TRABAJADOR": "nombre_en_documento",
    "NOMBRE Y APELLIDOS": "nombre_en_documento", "ANTIGÜEDAD": "fecha_antiguedad",
    "PERIODO DE LIQUIDACIÓN": "_fecha_documento", "TOTAL DEVENGADO": "nomina_bruto",
    "LÍQUIDO TOTAL A PERCIBIR": "nomina_liquido", "TIPO DE CONTRATO": "tipo_contrato",
    "FECHA DE INICIO": "fecha_inicio_contrato", "FECHA DE EMISIÓN": "_fecha_documento",
    "TOTAL DÍAS DE ALTA": "dias_cotizados", "RENDIMIENTO NETO REDUCIDO DEL TRABAJO": "rendimiento_neto_trabajo",
    "BASE IMPONIBLE GENERAL": "base_imponible_general", "FECHA DE PRESENTACIÓN": "_fecha_documento",
    "SALDO FINAL": "saldo_cuenta", "ENTIDAD": "entidad_prestamo", "FECHA DE CARGO": "_fecha_documento",
    "CUOTA MENSUAL": "cuota_prestamo", "CAPITAL PENDIENTE": "saldo_prestamo",
    "PRECIO TOTAL DE LA COMPRAVENTA": "precio_compra", "CANTIDAD EN CONCEPTO DE ARRAS": "importe_arras",
    "FECHA": "_fecha_documento", "REFERENCIA CATASTRAL": "referencia_catastral",
}


class ClienteIASimulado:
    def __init__(self, inventar: bool = False, error=None):
        self.llamadas: list[dict] = []
        self.inventar = inventar
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._crear))
        self.messages = SimpleNamespace(create=self._crear)

    def _crear(self, **kw):
        self.llamadas.append(kw)
        if self.error is not None:
            raise self.error
        texto = kw["messages"][0]["content"]
        fijado = re.search(r"El tipo de documento está fijado por la usuaria: (\w+)", texto)
        tipo = fijado.group(1) if fijado else next((t for k, t in CLASIFICACION if k in texto.upper()), "desconocido")
        campos, pagina, nombre, apellidos = [], 1, None, None
        for linea in texto.splitlines():
            m = re.match(r"=== PÁGINA (\d+) ===", linea)
            if m:
                pagina = int(m.group(1))
                continue
            if ":" not in linea:
                continue
            etiqueta, valor = (x.strip() for x in linea.split(":", 1))
            etiqueta = etiqueta.upper()
            if etiqueta == "NOMBRE":
                nombre = (valor, linea, pagina)
            elif etiqueta == "APELLIDOS":
                apellidos = valor
            elif etiqueta in ETIQUETAS:
                clave = ETIQUETAS[etiqueta]
                if clave == "_fecha_documento":
                    fechas = re.findall(r"\d{2}/\d{2}/\d{4}", valor)
                    if not fechas:
                        continue
                    valor = fechas[-1]
                if clave == "dni_nie" and tipo not in ("dni", "nie", "nomina"):
                    continue
                campos.append({"clave": clave, "valor": valor.rstrip(" €"), "pagina": pagina, "evidencia": linea})
        if nombre and apellidos:
            campos.append({"clave": "nombre_completo", "valor": f"{nombre[0]} {apellidos}", "pagina": nombre[2], "evidencia": nombre[1]})
        if self.inventar:
            campos.append({"clave": "precio_compra", "valor": "999.999,00", "pagina": 1, "evidencia": "PRECIO: 999.999,00"})
            campos.append({"clave": "nomina_liquido", "valor": "9.999,00", "pagina": 1, "evidencia": campos[0]["evidencia"] if campos else "x"})
        datos = {"tipo_documento": tipo, "motivo_clasificacion": "simulado", "campos": campos}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(datos, ensure_ascii=False))])
