"""Catálogos de dominio: operaciones, perfiles, tipos de documento y campos."""
from __future__ import annotations

TIPOS_OPERACION = {
    "compra_vivienda_habitual": "Compra de vivienda habitual",
    "compra_segunda_vivienda": "Compra de segunda vivienda",
    "subrogacion_cambio_banco": "Subrogación / cambio de banco",
    "refinanciacion": "Refinanciación / ampliación",
    "autopromocion": "Autopromoción",
    "otra": "Otra",
}

PERFILES_LABORALES = {
    "asalariado_indefinido": "Asalariado indefinido",
    "asalariado_temporal": "Asalariado temporal",
    "funcionario": "Funcionario",
    "autonomo": "Autónomo",
    "pensionista": "Pensionista",
    "otro": "Otro",
}

ESTADOS_EXPEDIENTE = {"abierto": "Abierto", "en_estudio": "En estudio", "cerrado": "Cerrado"}

CATEGORIAS = {
    "identificacion": "Identificación",
    "laboral": "Laboral",
    "economica_fiscal": "Económica / fiscal",
    "endeudamiento": "Endeudamiento",
    "vivienda": "Vivienda",
    "informes": "Informes",
    "otros": "Otros",
}

# tipo_documento -> (etiqueta, categoría)
TIPOS_DOCUMENTO = {
    "dni": ("DNI", "identificacion"),
    "nie": ("NIE / TIE", "identificacion"),
    "nomina": ("Nómina", "laboral"),
    "contrato_trabajo": ("Contrato de trabajo", "laboral"),
    "vida_laboral": ("Informe de vida laboral", "laboral"),
    "certificado_empresa": ("Certificado de empresa", "laboral"),
    "certificado_pension": ("Certificado de pensión", "laboral"),
    "irpf": ("Declaración IRPF (modelo 100)", "economica_fiscal"),
    "modelo_130": ("Modelo 130 (pago fraccionado)", "economica_fiscal"),
    "modelo_303": ("Modelo 303 (IVA)", "economica_fiscal"),
    "extracto_bancario": ("Extracto bancario", "economica_fiscal"),
    "recibo_prestamo": ("Recibo / contrato de préstamo", "endeudamiento"),
    "cirbe": ("Informe CIRBE", "endeudamiento"),
    "contrato_arras": ("Contrato de arras / reserva", "vivienda"),
    "nota_simple": ("Nota simple registral", "vivienda"),
    "tasacion": ("Informe de tasación", "vivienda"),
    "escritura": ("Escritura", "vivienda"),
    "desconocido": ("Sin clasificar", "otros"),
}

ESTADOS_DOCUMENTO = {
    "recibido": "Recibido",
    "pendiente_analisis": "Pendiente de análisis (IA)",
    "procesado": "Procesado (pendiente de revisión)",
    "revisado": "Revisado",
    "duplicado": "Duplicado",
    "incidencia": "Con incidencia",
    "descartado": "Descartado (no se usa)",
    "en_bandeja_incidencias": "En bandeja de incidencias",
}

# Estado de cada dato
ESTADOS_CAMPO = {
    "acreditado": "Acreditado documentalmente",
    "declarado": "Declarado por el cliente",
    "calculado": "Calculado",
    "pendiente_confirmar": "Pendiente de confirmar",
    "descartado": "Descartado",
}

# clave -> (etiqueta, tipo, ámbito, múltiple)
# tipo: importe | fecha | texto | entero | porcentaje
CAMPOS = {
    # Titular
    "nombre_completo": ("Nombre completo", "texto", "titular", False),
    "nombre_en_documento": ("Nombre que figura en el documento", "texto", "titular", True),
    "dni_nie": ("DNI / NIE", "texto", "titular", False),
    "fecha_nacimiento": ("Fecha de nacimiento", "fecha", "titular", False),
    "validez_documento": ("Validez del documento de identidad", "fecha", "titular", False),
    "empresa": ("Empresa", "texto", "titular", False),
    "cif_empresa": ("CIF de la empresa", "texto", "titular", False),
    "tipo_contrato": ("Tipo de contrato", "texto", "titular", False),
    "fecha_antiguedad": ("Fecha de antigüedad", "fecha", "titular", False),
    "fecha_inicio_contrato": ("Inicio del contrato", "fecha", "titular", False),
    "nomina_liquido": ("Líquido de nómina", "importe", "titular", True),
    "nomina_bruto": ("Bruto de nómina (total devengado)", "importe", "titular", True),
    "pagas_anuales": ("Pagas anuales", "entero", "titular", False),
    "ingresos_netos_mensuales": ("Ingresos netos mensuales", "importe", "titular", False),
    "otros_ingresos_mensuales": ("Otros ingresos netos mensuales", "importe", "titular", False),
    "pension_mensual": ("Pensión mensual", "importe", "titular", False),
    "ejercicio_irpf": ("Ejercicio IRPF", "entero", "titular", False),
    "rendimiento_neto_trabajo": ("Rendimiento neto del trabajo (IRPF)", "importe", "titular", False),
    "base_imponible_general": ("Base imponible general (IRPF)", "importe", "titular", False),
    "dias_cotizados": ("Días cotizados", "entero", "titular", False),
    "cuota_prestamo": ("Cuota mensual de préstamo existente", "importe", "titular", True),
    "saldo_prestamo": ("Saldo pendiente de préstamo", "importe", "titular", True),
    "entidad_prestamo": ("Entidad del préstamo", "texto", "titular", True),
    "saldo_cuenta": ("Saldo en cuenta", "importe", "titular", True),
    # Operación (expediente)
    "precio_compra": ("Precio de compra", "importe", "expediente", False),
    "valor_tasacion": ("Valor de tasación", "importe", "expediente", False),
    "importe_solicitado": ("Importe de préstamo solicitado", "importe", "expediente", False),
    "plazo_anios": ("Plazo (años)", "entero", "expediente", False),
    "tipo_interes_anual": ("Tipo de interés anual (%)", "porcentaje", "expediente", False),
    "ahorros_aportados": ("Ahorros aportados", "importe", "expediente", False),
    "importe_arras": ("Importe de arras / señal", "importe", "expediente", False),
    "porcentaje_impuestos_compra": ("Impuestos de compra (%)", "porcentaje", "expediente", False),
    "gastos_fijos_estimados": ("Gastos fijos estimados", "importe", "expediente", False),
    "referencia_catastral": ("Referencia catastral", "texto", "expediente", False),
}


def etiqueta_tipo_doc(tipo: str) -> str:
    return TIPOS_DOCUMENTO.get(tipo, (tipo, "otros"))[0]


def categoria_de(tipo: str) -> str:
    return TIPOS_DOCUMENTO.get(tipo, ("", "otros"))[1]


def etiqueta_campo(clave: str) -> str:
    return CAMPOS.get(clave, (clave,))[0]


# Descripciones que se usan como instrucciones para la extracción con IA.
DESCRIPCION_TIPOS = {
    "dni": "Documento Nacional de Identidad español (anverso y/o reverso).",
    "nie": "NIE, TIE o permiso de residencia de extranjero.",
    "nomina": "Nómina o recibo de salarios de un mes concreto.",
    "contrato_trabajo": "Contrato de trabajo o sus prórrogas.",
    "vida_laboral": "Informe de vida laboral de la Seguridad Social.",
    "certificado_empresa": "Certificado emitido por la empresa (antigüedad, salario, situación laboral).",
    "certificado_pension": "Certificado de pensión o revalorización (INSS u otros).",
    "irpf": "Declaración de la renta (modelo 100) o certificado de IRPF de la AEAT.",
    "modelo_130": "Pago fraccionado del IRPF de autónomos (modelo 130).",
    "modelo_303": "Autoliquidación trimestral de IVA (modelo 303).",
    "extracto_bancario": "Extracto o certificado de movimientos/saldo de una cuenta bancaria.",
    "recibo_prestamo": "Recibo, cuadro de amortización o contrato de un préstamo o hipoteca vigente.",
    "cirbe": "Informe de la Central de Información de Riesgos del Banco de España (CIRBE).",
    "contrato_arras": "Contrato de arras, reserva u oferta de compraventa de un inmueble.",
    "nota_simple": "Nota simple informativa del Registro de la Propiedad.",
    "tasacion": "Informe o certificado de tasación de un inmueble.",
    "escritura": "Escritura notarial (compraventa, préstamo hipotecario…).",
    "desconocido": "Ninguno de los anteriores o no se puede determinar con seguridad.",
}

DESCRIPCION_CAMPOS = {
    "nombre_completo": "Nombre y apellidos del titular tal como figuran en su documento de identidad.",
    "nombre_en_documento": "Nombre de la persona (trabajador, contribuyente, titular) que figura en el documento.",
    "dni_nie": "Número de DNI o NIE de la persona del documento (8 dígitos + letra, o X/Y/Z + 7 dígitos + letra).",
    "fecha_nacimiento": "Fecha de nacimiento.",
    "validez_documento": "Fecha de validez o caducidad del documento de identidad.",
    "empresa": "Razón social de la empresa empleadora.",
    "cif_empresa": "CIF/NIF de la empresa empleadora.",
    "tipo_contrato": "Modalidad del contrato: indefinido, temporal, fijo discontinuo, funcionario…",
    "fecha_antiguedad": "Fecha de antigüedad en la empresa.",
    "fecha_inicio_contrato": "Fecha de inicio del contrato de trabajo.",
    "nomina_liquido": "Importe líquido a percibir de la nómina (neto).",
    "nomina_bruto": "Total devengado (bruto) de la nómina.",
    "pagas_anuales": "Número de pagas anuales si figura expresamente (12, 14…).",
    "ingresos_netos_mensuales": "Ingresos netos mensuales si figuran expresamente como tales (p. ej. en un certificado).",
    "otros_ingresos_mensuales": "Otros ingresos netos mensuales que figuren expresamente (alquileres, prestaciones…).",
    "pension_mensual": "Importe mensual de la pensión.",
    "ejercicio_irpf": "Año del ejercicio fiscal declarado.",
    "rendimiento_neto_trabajo": "Rendimiento neto (reducido) del trabajo en la declaración de IRPF.",
    "base_imponible_general": "Base imponible general de la declaración de IRPF.",
    "dias_cotizados": "Total de días cotizados o de alta en la vida laboral.",
    "cuota_prestamo": "Cuota mensual de un préstamo existente (una entrada por préstamo).",
    "saldo_prestamo": "Capital o saldo pendiente de un préstamo existente (una entrada por préstamo).",
    "entidad_prestamo": "Entidad acreedora de un préstamo existente (una entrada por préstamo).",
    "saldo_cuenta": "Saldo final o disponible de una cuenta bancaria.",
    "precio_compra": "Precio total de compraventa del inmueble.",
    "valor_tasacion": "Valor de tasación del inmueble.",
    "importe_solicitado": "Importe del préstamo hipotecario solicitado, si figura en el documento.",
    "plazo_anios": "Plazo del préstamo en años, si figura en el documento.",
    "tipo_interes_anual": "Tipo de interés nominal anual (%), si figura en el documento.",
    "ahorros_aportados": "Ahorros o fondos propios aportados a la compra, si figuran expresamente.",
    "importe_arras": "Importe entregado en concepto de arras o señal.",
    "porcentaje_impuestos_compra": "Porcentaje de impuestos de la compra, si figura expresamente.",
    "gastos_fijos_estimados": "Gastos de notaría, registro, gestoría y tasación, si figuran expresamente.",
    "referencia_catastral": "Referencia catastral del inmueble (20 caracteres).",
}
