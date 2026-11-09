"""Genera documentos y correos FICTICIOS para probar el flujo de extremo a extremo.

Todos los datos son inventados (persona «Lucía Prueba Ficticia», DNI de prueba 12345678Z).
Uso:
    .venv\\Scripts\\python scripts\\generar_documentos_ficticios.py --referencia EXP-2026-0001
Salida: ejemplos/documentos_ficticios/ y ejemplos/correos_ficticios/
"""
from __future__ import annotations

import argparse
import calendar
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AVISO = "DOCUMENTO FICTICIO - SOLO PARA PRUEBAS - NO TIENE VALIDEZ"


def pdf(ruta: Path, titulo: str, lineas: list[str]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(ruta), pagesize=A4)
    c.setTitle(titulo)
    ancho, alto = A4
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, alto - 30, AVISO)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, alto - 60, titulo)
    c.setFont("Helvetica", 10)
    y = alto - 90
    for linea in lineas:
        if y < 60:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = alto - 60
        c.drawString(50, y, linea)
        y -= 16
    c.save()


def imagen_texto(ruta: Path, lineas: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(img)
    try:
        fuente = ImageFont.truetype("arial.ttf", 36)
    except OSError:
        fuente = ImageFont.load_default()
    y = 40
    for linea in [AVISO, *lineas]:
        d.text((40, y), linea, fill="black", font=fuente)
        y += 60
    img.save(ruta)


def fin_de_mes(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def meses_atras(n: int) -> date:
    hoy = date.today().replace(day=1)
    for _ in range(n):
        hoy = (hoy - timedelta(days=1)).replace(day=1)
    return hoy


MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]


def generar(destino: Path) -> list[Path]:
    destino.mkdir(parents=True, exist_ok=True)
    hoy = date.today()
    f = lambda d: d.strftime("%d/%m/%Y")  # noqa: E731
    creados = []

    def nuevo(nombre):
        p = destino / nombre
        creados.append(p)
        return p

    pdf(nuevo("DNI_Lucia_Prueba.pdf"), "DOCUMENTO NACIONAL DE IDENTIDAD", [
        "REINO DE ESPAÑA",
        "APELLIDOS: PRUEBA FICTICIA",
        "NOMBRE: LUCÍA",
        "DNI: 12345678Z",
        "FECHA DE NACIMIENTO: 14/05/1988",
        f"VÁLIDO HASTA: {f(hoy.replace(year=hoy.year + 5))}",
        "IDESP ZZZ000000",
    ])
    liquidos = ["2.150,40", "2.148,90", "2.152,10"]
    for i, liquido in enumerate(liquidos, start=1):
        mes = meses_atras(i)
        pdf(nuevo(f"nomina_{mes:%Y_%m}.pdf"), "RECIBO INDIVIDUAL DE SALARIOS (NÓMINA)", [
            "EMPRESA: Empresa Ficticia de Pruebas S.L.",
            "CIF: B12345674",
            "TRABAJADORA: Lucía Prueba Ficticia",
            "DNI: 12345678Z",
            "ANTIGÜEDAD: 01/03/2019",
            f"PERIODO DE LIQUIDACIÓN: 01/{mes:%m/%Y} A {f(fin_de_mes(mes))}",
            "Salario base ........................ 2.400,00",
            "Complementos ......................... 450,00",
            "TOTAL DEVENGADO: 2.850,00",
            "TOTAL A DEDUCIR: 700,00",
            f"LÍQUIDO TOTAL A PERCIBIR: {liquido} €",
        ])
    pdf(nuevo("contrato_trabajo.pdf"), "CONTRATO DE TRABAJO", [
        "EMPRESA: Empresa Ficticia de Pruebas S.L.",
        "TRABAJADORA: Lucía Prueba Ficticia",
        "TIPO DE CONTRATO: INDEFINIDO A TIEMPO COMPLETO",
        "FECHA DE INICIO: 01/03/2019",
        "CLÁUSULAS: ... (texto ficticio) ...",
        "FECHA DE FIRMA: 25/02/2019",
    ])
    pdf(nuevo("vida_laboral.pdf"), "INFORME DE VIDA LABORAL", [
        "TESORERÍA GENERAL DE LA SEGURIDAD SOCIAL (simulado)",
        "NOMBRE Y APELLIDOS: Lucía Prueba Ficticia",
        f"FECHA DE EMISIÓN: {f(hoy - timedelta(days=5))}",
        "TOTAL DÍAS DE ALTA: 4.215",
    ])
    pdf(nuevo("irpf_ejercicio_anterior.pdf"), "IMPUESTO SOBRE LA RENTA DE LAS PERSONAS FÍSICAS - MODELO 100", [
        f"EJERCICIO {hoy.year - 1}",
        "Titular: Lucía Prueba Ficticia - 12345678Z",
        "RENDIMIENTO NETO REDUCIDO DEL TRABAJO: 31.250,00",
        "BASE IMPONIBLE GENERAL: 31.250,00",
        f"FECHA DE PRESENTACIÓN: {f(date(hoy.year, 5, 20) if hoy.month > 5 else date(hoy.year - 1, 5, 20))}",
    ])
    pdf(nuevo("extracto_bancario.pdf"), "EXTRACTO DE MOVIMIENTOS DE CUENTA", [
        "Entidad: Banco Ficticio de Pruebas",
        "IBAN: ES00 0000 0000 0000 0000 0000",
        f"FECHA DE EMISIÓN: {f(hoy - timedelta(days=10))}",
        "SALDO ANTERIOR: 41.200,00",
        "SALDO FINAL: 43.500,00",
    ])
    pdf(nuevo("recibo_prestamo_coche.pdf"), "RECIBO DE PRÉSTAMO PERSONAL", [
        "ENTIDAD: Financiera Ficticia S.A.",
        "Titular: Lucía Prueba Ficticia",
        f"FECHA DE CARGO: {f(hoy - timedelta(days=20))}",
        "CUOTA MENSUAL: 245,30 €",
        "CAPITAL PENDIENTE: 8.430,00 €",
    ])
    pdf(nuevo("contrato_arras.pdf"), "CONTRATO DE ARRAS PENITENCIALES", [
        "PARTE VENDEDORA: Vendedor Ficticio",
        "PARTE COMPRADORA: Lucía Prueba Ficticia",
        "Inmueble: Calle Inventada 1, 3º B, Ciudad Ficticia",
        "PRECIO TOTAL DE LA COMPRAVENTA: 240.000,00 €",
        "CANTIDAD EN CONCEPTO DE ARRAS: 12.000,00 €",
        f"FECHA: {f(hoy - timedelta(days=15))}",
    ])
    pdf(nuevo("nota_simple.pdf"), "NOTA SIMPLE INFORMATIVA", [
        "REGISTRO DE LA PROPIEDAD DE CIUDAD FICTICIA (simulado)",
        "FINCA: 99999",
        "REFERENCIA CATASTRAL: 0000000AA0000A0000AA",
        f"FECHA DE EMISIÓN: {f(hoy - timedelta(days=12))}",
    ])
    imagen_texto(nuevo("dni_escaneado_prueba.png"), [
        "DOCUMENTO NACIONAL DE IDENTIDAD",
        "APELLIDOS: PRUEBA FICTICIA",
        "NOMBRE: LUCIA",
        "DNI: 12345678Z",
    ])
    nuevo("notas_cliente.txt").write_text("Formato no admitido (prueba de incidencia).\n", encoding="utf-8")
    nuevo("pdf_danado.pdf").write_bytes(b"%PDF-1.4\n%%% contenido corrupto de prueba %%%\n")
    return creados


def correo(ruta: Path, remitente: str, asunto: str, adjuntos: list[Path]) -> None:
    msg = EmailMessage()
    msg["From"] = remitente
    msg["To"] = "intermediaria@ejemplo.invalid"
    msg["Subject"] = asunto
    msg["Date"] = format_datetime(datetime.now().astimezone())
    msg["Message-ID"] = make_msgid(domain="ficticio.invalid")
    msg.set_content("Correo ficticio de prueba. Adjunto la documentación.\n")
    for a in adjuntos:
        tipo = {"pdf": ("application", "pdf"), "png": ("image", "png"), "txt": ("text", "plain")}[a.suffix.lstrip(".")]
        msg.add_attachment(a.read_bytes(), maintype=tipo[0], subtype=tipo[1], filename=a.name)
    ruta.write_bytes(bytes(msg))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--referencia", default=f"EXP-{date.today().year}-0001")
    p.add_argument("--remitente", default="lucia.prueba@ejemplo.invalid")
    args = p.parse_args()
    docs = generar(RAIZ / "ejemplos" / "documentos_ficticios")
    por_nombre = {d.name: d for d in docs}
    dir_correos = RAIZ / "ejemplos" / "correos_ficticios"
    dir_correos.mkdir(parents=True, exist_ok=True)
    laboral = [d for d in docs if d.name.startswith(("nomina", "contrato_trabajo", "vida_laboral"))]
    correo(dir_correos / "1_con_referencia.eml", f"Lucía Prueba <{args.remitente}>",
           f"Documentación hipoteca [{args.referencia}]", [por_nombre["DNI_Lucia_Prueba.pdf"], *laboral])
    correo(dir_correos / "2_desde_remitente_registrado.eml", f"Lucía Prueba <{args.remitente}>",
           "Más papeles que me pediste", [por_nombre[n] for n in ("irpf_ejercicio_anterior.pdf", "extracto_bancario.pdf",
                                                                    "recibo_prestamo_coche.pdf", "contrato_arras.pdf", "nota_simple.pdf")])
    correo(dir_correos / "3_remitente_desconocido.eml", "Desconocido <alguien@otro.invalid>",
           "Hola, te paso esto", [por_nombre["extracto_bancario.pdf"]])
    correo(dir_correos / "4_duplicado_y_errores.eml", f"Lucía Prueba <{args.remitente}>",
           f"Reenvío [{args.referencia}]", [por_nombre["DNI_Lucia_Prueba.pdf"], por_nombre["notas_cliente.txt"],
                                           por_nombre["pdf_danado.pdf"], por_nombre["dni_escaneado_prueba.png"]])
    print(f"Documentos ficticios: {RAIZ / 'ejemplos' / 'documentos_ficticios'}")
    print(f"Correos ficticios (.eml): {dir_correos}")


if __name__ == "__main__":
    main()
