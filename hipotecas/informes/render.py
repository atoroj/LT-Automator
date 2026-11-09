"""Renderizado del informe a DOCX (python-docx) y PDF (reportlab). Sin marca comercial."""
from __future__ import annotations

import io

AVISO = (
    "Documento interno de trabajo elaborado a partir de la documentación aportada. Los cálculos son "
    "estimaciones orientativas obtenidas con los supuestos indicados y no constituyen una oferta, "
    "aprobación ni decisión de financiación."
)
LEYENDA = (
    "Estado de los datos — Acreditado: verificado en un documento aportado. Declarado: manifestado por el "
    "cliente, sin acreditar. Calculado: derivado de otros datos. Pendiente de confirmar: extraído "
    "automáticamente y aún no revisado. Supuesto de configuración: valor por defecto, no facilitado por el cliente."
)


def _marca(aprobado: dict | None) -> str:
    if aprobado:
        return f"INFORME APROBADO por la intermediaria el {aprobado['fecha_txt']}"
    return "BORRADOR — Pendiente de revisión y aprobación. No distribuir."


def _secciones(c: dict):
    """Secuencia común: (tipo, datos) para ambos formatos."""
    m = c["meta"]
    yield "h1", "Informe de operación hipotecaria"
    yield "tabla", (["Concepto", "Valor"], [
        ["Referencia del expediente", m["referencia"]],
        ["Expediente", m["nombre"]],
        ["Tipo de operación", m["tipo_operacion"]],
        ["Versión del informe", str(m["version"])],
        ["Fecha de generación", m["generado_txt"]],
    ])
    yield "p_peq", LEYENDA

    yield "h2", "1. Titulares: situación personal, laboral y económica"
    if not c["titulares"]:
        yield "p", "No se han registrado titulares."
    for t in c["titulares"]:
        yield "h3", f"{t['nombre']} — {t['perfil']} — {t['dni']}"
        yield "tabla", (["Dato", "Valor", "Estado", "Fuente"], t["filas"])

    yield "h2", "2. Endeudamiento existente"
    hay = False
    for t in c["titulares"]:
        if t["deudas"]:
            hay = True
            yield "h3", t["nombre"]
            yield "tabla", (["Entidad", "Cuota mensual", "Saldo pendiente", "Estado", "Fuente"], t["deudas"])
    if not hay:
        yield "p", "No constan préstamos vigentes acreditados ni declarados."

    yield "h2", "3. Operación"
    yield "tabla", (["Dato", "Valor", "Estado", "Fuente"], c["operacion"])

    yield "h2", "4. Simulación de la cuota"
    yield "tabla", (["Concepto", "Valor"], c["simulacion"])

    yield "h2", "5. Ratios y aportación de fondos"
    yield "tabla", (["Indicador", "Valor", "Referencia"], c["ratios"])

    yield "h2", "6. Observaciones"
    if c["observaciones"].strip():
        for parrafo in c["observaciones"].strip().split("\n"):
            if parrafo.strip():
                yield "p", parrafo.strip()
    else:
        yield "p", "Sin observaciones de la intermediaria."
    if c["alertas"]:
        yield "h3", "Indicadores que superan los umbrales configurados"
        for x in c["alertas"]:
            yield "li", x
    if c["notas_calculo"]:
        yield "h3", "Notas sobre los cálculos"
        for x in c["notas_calculo"]:
            yield "li", x

    yield "h2", "7. Documentación pendiente"
    if c["documentos_pendientes"]:
        yield "tabla", (["Requisito", "Titular", "Estado", "Detalle"], c["documentos_pendientes"])
    else:
        yield "p", "No hay documentación pendiente según los requisitos definidos para este expediente."

    yield "h2", "8. Cuestiones por confirmar"
    if c["cuestiones"]:
        for x in c["cuestiones"]:
            yield "li", x
    else:
        yield "p", "No hay cuestiones pendientes de confirmar."

    yield "h2", "Anexo. Documentación aportada"
    if c["documentos_aportados"]:
        yield "tabla", (["Doc.", "Tipo", "Titular", "Fecha doc.", "Recepción", "Procedencia"], c["documentos_aportados"])
    else:
        yield "p", "No se ha aportado documentación."
    yield "p_peq", AVISO


def a_docx(c: dict, aprobado: dict | None = None) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    doc = Document()
    estilo = doc.styles["Normal"]
    estilo.font.name = "Calibri"
    estilo.font.size = Pt(10)
    seccion = doc.sections[0]
    cab = seccion.header.paragraphs[0]
    cab.text = _marca(aprobado)
    cab.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cab.runs[0].font.bold = True
    cab.runs[0].font.color.rgb = RGBColor(0x1F, 0x6F, 0x3A) if aprobado else RGBColor(0xB0, 0x30, 0x30)
    pie = seccion.footer.paragraphs[0]
    pie.text = f"{c['meta']['referencia']} · v{c['meta']['version']} · Documento confidencial de uso interno"
    pie.runs[0].font.size = Pt(8)

    for tipo, datos in _secciones(c):
        if tipo == "h1":
            doc.add_heading(datos, level=0)
        elif tipo == "h2":
            doc.add_heading(datos, level=1)
        elif tipo == "h3":
            doc.add_heading(datos, level=2)
        elif tipo == "p":
            doc.add_paragraph(datos)
        elif tipo == "p_peq":
            p = doc.add_paragraph()
            r = p.add_run(datos)
            r.font.size = Pt(8)
            r.italic = True
        elif tipo == "li":
            doc.add_paragraph(datos, style="List Bullet")
        elif tipo == "tabla":
            cab_t, filas = datos
            tabla = doc.add_table(rows=1, cols=len(cab_t))
            tabla.style = "Light Grid Accent 1"
            for i, h in enumerate(cab_t):
                tabla.rows[0].cells[i].text = h
            for fila in filas:
                celdas = tabla.add_row().cells
                for i, v in enumerate(fila):
                    celdas[i].text = str(v)
            doc.add_paragraph()
    if aprobado and aprobado.get("nota"):
        doc.add_heading("Nota de aprobación", level=1)
        doc.add_paragraph(aprobado["nota"])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def a_pdf(c: dict, aprobado: dict | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    est = getSampleStyleSheet()
    celda = ParagraphStyle("celda", parent=est["BodyText"], fontSize=8, leading=10)
    celda_b = ParagraphStyle("celda_b", parent=celda, fontName="Helvetica-Bold")
    peq = ParagraphStyle("peq", parent=est["BodyText"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555"))
    buf = io.BytesIO()
    marca = _marca(aprobado)
    pie_txt = f"{c['meta']['referencia']} · v{c['meta']['version']} · Documento confidencial de uso interno"

    def decorar(canvas, documento):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 9)
        canvas.setFillColor(colors.HexColor("#1F6F3A") if aprobado else colors.HexColor("#B03030"))
        canvas.drawCentredString(A4[0] / 2, A4[1] - 1.2 * cm, marca)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(2 * cm, 1.2 * cm, pie_txt)
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {documento.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm, bottomMargin=2 * cm,
                            title=f"Informe {c['meta']['referencia']}", author="")
    ancho = A4[0] - 4 * cm
    hist = []
    for tipo, datos in _secciones(c):
        if tipo == "h1":
            hist.append(Paragraph(escape(datos), est["Title"]))
        elif tipo == "h2":
            hist.append(Spacer(1, 6))
            hist.append(Paragraph(escape(datos), est["Heading2"]))
        elif tipo == "h3":
            hist.append(Paragraph(escape(datos), est["Heading4"]))
        elif tipo == "p":
            hist.append(Paragraph(escape(datos), est["BodyText"]))
        elif tipo == "p_peq":
            hist.append(Spacer(1, 4))
            hist.append(Paragraph(escape(datos), peq))
        elif tipo == "li":
            hist.append(ListFlowable([ListItem(Paragraph(escape(datos), est["BodyText"]))], bulletType="bullet", leftIndent=12))
        elif tipo == "tabla":
            cab_t, filas = datos
            n = len(cab_t)
            pesos = {2: [0.55, 0.45], 3: [0.5, 0.25, 0.25], 4: [0.28, 0.2, 0.2, 0.32],
                     5: [0.22, 0.17, 0.17, 0.18, 0.26], 6: [0.08, 0.26, 0.2, 0.14, 0.14, 0.18]}[n]
            data = [[Paragraph(escape(h), celda_b) for h in cab_t]] + [
                [Paragraph(escape(str(v)), celda) for v in fila] for fila in filas
            ]
            t = Table(data, colWidths=[ancho * p for p in pesos], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B8C4D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FB")]),
            ]))
            hist.append(t)
            hist.append(Spacer(1, 6))
    if aprobado and aprobado.get("nota"):
        hist.append(Paragraph("Nota de aprobación", est["Heading2"]))
        hist.append(Paragraph(escape(aprobado["nota"]), est["BodyText"]))
    doc.build(hist, onFirstPage=decorar, onLaterPages=decorar)
    return buf.getvalue()
