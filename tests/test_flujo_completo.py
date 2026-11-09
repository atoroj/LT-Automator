"""Flujo de extremo a extremo con correos y documentos ficticios."""
import json
from decimal import Decimal
import os

import pytest

from hipotecas import almacenamiento as alm
from hipotecas import datos
from hipotecas import expedientes as exps
from hipotecas.checklist.evaluador import evaluar
from hipotecas.db import todos, uno
from hipotecas.informes import servicio as informes
from hipotecas.ingesta import Adjunto, Mensaje, asignar_incidencia_a_expediente, procesar_mensaje
from hipotecas.situacion import analizar


def _msg(mid, remitente, asunto, *rutas):
    return Mensaje(mid, "gmail", remitente, asunto, "2026-11-01T10:00:00",
                   [Adjunto(r.name, r.read_bytes(), f"{i}:{r.name}") for i, r in enumerate(rutas)])


@pytest.fixture
def expediente(conn, cfg):
    exp_id = exps.crear_expediente(conn, cfg, "Compra ficticia", "compra_vivienda_habitual", "lucia.prueba@ejemplo.invalid")
    exps.anadir_titular(conn, exp_id, "Lucía Prueba Ficticia", "asalariado_indefinido", "12345678Z")
    return exps.obtener(conn, exp_id)


def test_flujo_completo(conn, cfg, ocr, docs_ficticios, expediente, proveedor_ia):
    d = docs_ficticios
    prov = proveedor_ia
    ref = expediente["referencia"]
    nominas = [p for n, p in sorted(d.items()) if n.startswith("nomina")]

    # 1) Correo con referencia en el asunto
    r = procesar_mensaje(conn, cfg, _msg("m1", "Lucía <lucia.prueba@ejemplo.invalid>", f"Docs [{ref}]",
                                         d["DNI_Lucia_Prueba.pdf"], *nominas, d["contrato_trabajo.pdf"], d["vida_laboral.pdf"]), prov, ocr)
    assert r.estado == "procesado" and r.expediente_id == expediente["id"]
    assert len(r.documentos) == 6

    # Idempotencia: reprocesar el mismo mensaje no duplica nada
    assert procesar_mensaje(conn, cfg, _msg("m1", "x", "y", d["DNI_Lucia_Prueba.pdf"]), prov, ocr).estado == "ya_procesado"
    assert uno(conn, "SELECT COUNT(*) n FROM documentos")["n"] == 6

    # 2) Correo sin referencia pero desde remitente registrado
    r = procesar_mensaje(conn, cfg, _msg("m2", "lucia.prueba@ejemplo.invalid", "más papeles",
                                         d["irpf_ejercicio_anterior.pdf"], d["extracto_bancario.pdf"], d["recibo_prestamo_coche.pdf"],
                                         d["contrato_arras.pdf"], d["nota_simple.pdf"]), prov, ocr)
    assert r.estado == "procesado" and r.expediente_id == expediente["id"]

    # 3) Remitente desconocido -> bandeja de incidencias, sin asociación
    r = procesar_mensaje(conn, cfg, _msg("m3", "alguien@otro.invalid", "hola", d["extracto_bancario.pdf"]), prov, ocr)
    assert r.estado == "incidencia" and r.expediente_id is None
    inc = uno(conn, "SELECT * FROM incidencias WHERE gmail_mensaje_id='m3'")
    assert inc["tipo"] == "correo_sin_expediente"

    # 4) Duplicado + formato no admitido + PDF dañado + imagen sin OCR
    r = procesar_mensaje(conn, cfg, _msg("m4", "lucia.prueba@ejemplo.invalid", f"Reenvío {ref}",
                                         d["DNI_Lucia_Prueba.pdf"], d["notas_cliente.txt"], d["pdf_danado.pdf"], d["dni_escaneado_prueba.png"]), prov, ocr)
    estados = {x["nombre_original"]: x["estado"] for x in todos(conn, "SELECT * FROM documentos WHERE gmail_mensaje_id='m4'")}
    assert estados["DNI_Lucia_Prueba.pdf"] == "duplicado"
    assert estados["notas_cliente.txt"] == "incidencia"
    assert estados["pdf_danado.pdf"] == "incidencia"
    tipos_inc = {x["tipo"] for x in todos(conn, "SELECT tipo FROM incidencias")}
    assert {"formato_no_admitido", "documento_ilegible", "requiere_ocr"} <= tipos_inc

    # Originales inalterados, solo lectura, y copias de trabajo renombradas en su categoría
    for doc in todos(conn, "SELECT * FROM documentos WHERE expediente_id=? AND estado='procesado'", (expediente["id"],)):
        orig = alm.absoluta(cfg, doc["ruta_original"])
        assert alm.sha256_archivo(orig) == doc["sha256"]
        assert not os.access(orig, os.W_OK)
        assert cfg["expedientes"]["carpeta_entrada"] in doc["ruta_original"]
        assert doc["ruta_trabajo"] and cfg["expedientes"]["subcarpetas"][doc["categoria"]] in doc["ruta_trabajo"]

    # Clasificación y asignación de titular (único titular)
    tipos = {x["tipo_documento"] for x in todos(conn, "SELECT tipo_documento FROM documentos WHERE expediente_id=? AND estado='procesado'", (expediente["id"],))}
    assert {"dni", "nomina", "contrato_trabajo", "vida_laboral", "irpf", "extracto_bancario", "recibo_prestamo", "contrato_arras", "nota_simple"} <= tipos

    # Datos extraídos: pendientes de confirmar, con documento y página
    campos = datos.campos_expediente(conn, expediente["id"])
    assert campos and all(c["estado"] == "pendiente_confirmar" and c["documento_id"] for c in campos)
    assert {c["clave"] for c in campos} >= {"dni_nie", "nomina_liquido", "precio_compra", "cuota_prestamo"}

    # Checklist: CIRBE y certificado de pensión no se recibieron; solo CIRBE debe estar pendiente (pensión no aplica)
    ev = evaluar(conn, expediente["id"])
    por_codigo = {i.requisito["codigo"]: i for i in ev.items}
    assert por_codigo["CIRBE"].estado == "pendiente"
    assert "PENSION" not in por_codigo and "PAGOS_FRACCIONADOS" not in por_codigo
    assert por_codigo["NOMINAS"].estado == "ok"
    assert por_codigo["ID_TITULAR"].estado == "ok"
    assert por_codigo["ARRAS"].estado == "ok"
    assert len(ev.duplicados) == 1

    # Confirmación por la usuaria y datos declarados
    for c in campos:
        datos.confirmar(conn, c["id"])
    datos.anadir_manual(conn, expediente["id"], "importe_solicitado", "190.000,00", "declarado")
    datos.anadir_manual(conn, expediente["id"], "ahorros_aportados", "60.000", "declarado")

    a = analizar(conn, cfg, expediente["id"])
    t = a["titulares"][0]
    assert t["ingresos"].estado == "calculado" and not t["ingresos"].basado_en_pendientes
    assert a["deudas_totales"] == Decimal("245.30")
    res = a["resultado"]
    assert res.cuota is not None and res.ltv is not None and res.aportacion_necesaria is not None
    assert not a["entradas_pendientes"]

    # Informe: borrador -> aprobación explícita
    inf_id = informes.generar_borrador(conn, cfg, expediente["id"])
    inf = uno(conn, "SELECT * FROM informes WHERE id=?", (inf_id,))
    assert inf["estado"] == "borrador"
    assert alm.absoluta(cfg, inf["ruta_pdf"]).read_bytes()[:4] == b"%PDF"
    assert alm.absoluta(cfg, inf["ruta_docx"]).read_bytes()[:2] == b"PK"
    contenido = json.loads(inf["datos_json"])
    assert any(p[0].startswith("Informe CIRBE") for p in contenido["documentos_pendientes"])

    with pytest.raises(informes.AprobacionNoValida):
        informes.aprobar(conn, cfg, inf_id, False, ref)
    with pytest.raises(informes.AprobacionNoValida):
        informes.aprobar(conn, cfg, inf_id, True, "EXP-OTRA")
    informes.aprobar(conn, cfg, inf_id, True, ref, "Revisado")
    inf = uno(conn, "SELECT * FROM informes WHERE id=?", (inf_id,))
    assert inf["estado"] == "aprobado" and "FINAL" in inf["ruta_pdf"]
    with pytest.raises(informes.AprobacionNoValida):
        informes.aprobar(conn, cfg, inf_id, True, ref)

    # Resolver la incidencia del remitente desconocido asignándola manualmente
    nuevos = asignar_incidencia_a_expediente(conn, cfg, inc["id"], expediente["id"], prov, ocr)
    assert len(nuevos) == 1
    assert uno(conn, "SELECT estado FROM documentos WHERE id=?", (nuevos[0],))["estado"] == "duplicado"  # mismo extracto ya recibido
    assert uno(conn, "SELECT estado FROM incidencias WHERE id=?", (inc["id"],))["estado"] == "resuelta"

    # Los logs técnicos no contienen texto de documentos
    log = (cfg.dir_logs / "aplicacion.log")
    if log.exists():
        contenido_log = log.read_text(encoding="utf-8")
        assert "LÍQUIDO" not in contenido_log and "12345678Z" not in contenido_log


def test_asociacion_conservadora(conn, cfg, ocr, expediente):
    from hipotecas.ingesta import asociar

    ref = expediente["referencia"]
    assert asociar(conn, cfg, "x@y.invalid", f"[{ref}]")[0] == expediente["id"]
    assert asociar(conn, cfg, "x@y.invalid", "sin referencia")[0] is None
    assert asociar(conn, cfg, "x@y.invalid", "EXP-1999-0001")[0] is None
    otro = exps.crear_expediente(conn, cfg, "Otro", "refinanciacion", "lucia.prueba@ejemplo.invalid")
    # mismo remitente en dos expedientes -> no se decide
    assert asociar(conn, cfg, "lucia.prueba@ejemplo.invalid", "hola")[0] is None
    # dos referencias -> no se decide
    ref2 = exps.obtener(conn, otro)["referencia"]
    assert asociar(conn, cfg, "x@y.invalid", f"{ref} y {ref2}")[0] is None


def test_requisito_no_definido_no_es_pendiente(conn, cfg, expediente):
    from hipotecas.checklist.requisitos import fijar_modo_expediente

    ev = evaluar(conn, expediente["id"])
    codigos = {i.requisito["codigo"] for i in ev.items}
    assert "TASACION" not in codigos  # inactivo por defecto
    req = uno(conn, "SELECT id FROM requisitos WHERE codigo='TASACION'")
    fijar_modo_expediente(conn, expediente["id"], req["id"], "obligatorio")
    assert "TASACION" in {i.requisito["codigo"] for i in evaluar(conn, expediente["id"]).items}
    req = uno(conn, "SELECT id FROM requisitos WHERE codigo='CIRBE'")
    fijar_modo_expediente(conn, expediente["id"], req["id"], "no_aplica")
    assert "CIRBE" not in {i.requisito["codigo"] for i in evaluar(conn, expediente["id"]).items}


def test_documento_antiguo(conn, cfg, ocr, docs_ficticios, expediente, proveedor_ia):
    from hipotecas.documentos import procesamiento

    doc_id, _ = procesamiento.registrar(conn, cfg, expediente, "vida_laboral.pdf", docs_ficticios["vida_laboral.pdf"].read_bytes(),
                                        procesamiento.Procedencia(origen="subida_manual"))
    procesamiento.procesar(conn, cfg, doc_id, proveedor_ia, ocr)
    procesamiento.reclasificar(conn, cfg, doc_id, fecha_documento="2020-01-01")
    item = next(i for i in evaluar(conn, expediente["id"]).items if i.requisito["codigo"] == "VIDA_LABORAL")
    assert item.estado == "antiguo"
    # el original sigue intacto tras reclasificar
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    assert alm.sha256_archivo(alm.absoluta(cfg, doc["ruta_original"])) == doc["sha256"]


def test_incoherencia_nombre(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    from hipotecas.documentos import procesamiento

    exp_id = exps.crear_expediente(conn, cfg, "Incoherente", "compra_vivienda_habitual")
    exps.anadir_titular(conn, exp_id, "Pedro Otro Distinto", "asalariado_indefinido")
    exp = exps.obtener(conn, exp_id)
    doc_id, _ = procesamiento.registrar(conn, cfg, exp, "dni.pdf", docs_ficticios["DNI_Lucia_Prueba.pdf"].read_bytes(),
                                        procesamiento.Procedencia(origen="subida_manual"))
    procesamiento.procesar(conn, cfg, doc_id, proveedor_ia, ocr)
    ev = evaluar(conn, exp_id)
    assert any("no coincide" in i["mensaje"] for i in ev.incoherencias)
    item = next(i for i in ev.items if i.requisito["codigo"] == "ID_TITULAR")
    assert item.estado == "incoherente"


def test_sin_ia_queda_pendiente_y_se_analiza_despues(conn, cfg, ocr, docs_ficticios, expediente, proveedor_ia):
    """Sin IA no se clasifica ni se extrae nada por suposición; al activarla se analiza lo pendiente."""
    from hipotecas.documentos import procesamiento

    r = procesar_mensaje(conn, cfg, _msg("s1", "x@y.invalid", f"[{expediente['referencia']}]",
                                         docs_ficticios["contrato_arras.pdf"]), None, ocr)
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (r.documentos[0],))
    assert doc["estado"] == "pendiente_analisis" and doc["tipo_documento"] == "desconocido"
    assert doc["titular_id"] is not None  # titular único: asignación inequívoca
    assert doc["ruta_trabajo"] and alm.absoluta(cfg, doc["ruta_original"]).exists()
    assert not datos.campos_expediente(conn, expediente["id"])
    assert any(i.requisito["codigo"] == "ARRAS" and i.estado == "pendiente" for i in evaluar(conn, expediente["id"]).items)

    res = procesamiento.analizar_pendientes(conn, cfg, proveedor_ia)
    assert res == {"analizados": 1, "pendientes": 0}
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc["id"],))
    assert doc["estado"] == "procesado" and doc["tipo_documento"] == "contrato_arras"
    assert "05_Vivienda" in doc["ruta_trabajo"]
    assert {c["clave"] for c in datos.campos_expediente(conn, expediente["id"])} >= {"precio_compra", "importe_arras"}


def test_fallo_ia_crea_incidencia_y_reintenta(conn, cfg, ocr, docs_ficticios, expediente, proveedor_ia):
    import anthropic
    import httpx

    from hipotecas.documentos import procesamiento
    from hipotecas.extraccion.anthropic_proveedor import ProveedorAnthropic
    from simulador_ia import ClienteIASimulado

    roto = ProveedorAnthropic(cfg, cliente=ClienteIASimulado(error=anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.invalid"))))
    doc_id, _ = procesamiento.registrar(conn, cfg, expediente, "nota.pdf", docs_ficticios["nota_simple.pdf"].read_bytes(),
                                        procesamiento.Procedencia(origen="subida_manual"))
    procesamiento.procesar(conn, cfg, doc_id, roto, ocr)
    assert uno(conn, "SELECT estado FROM documentos WHERE id=?", (doc_id,))["estado"] == "pendiente_analisis"
    assert uno(conn, "SELECT tipo FROM incidencias WHERE documento_id=?", (doc_id,))["tipo"] == "error_ia"
    assert procesamiento.analizar_pendientes(conn, cfg, proveedor_ia)["analizados"] == 1
    assert uno(conn, "SELECT tipo_documento FROM documentos WHERE id=?", (doc_id,))["tipo_documento"] == "nota_simple"


def test_titular_por_nombre_con_varios_titulares(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    from hipotecas.documentos import procesamiento

    exp_id = exps.crear_expediente(conn, cfg, "Dos titulares", "compra_vivienda_habitual")
    exps.anadir_titular(conn, exp_id, "Otra Persona Distinta", "asalariado_indefinido")
    lucia = exps.anadir_titular(conn, exp_id, "Lucía Prueba Ficticia", "asalariado_indefinido")  # sin DNI registrado
    exp = exps.obtener(conn, exp_id)
    contrato = docs_ficticios["contrato_trabajo.pdf"]
    doc_id, _ = procesamiento.registrar(conn, cfg, exp, contrato.name, contrato.read_bytes(),
                                        procesamiento.Procedencia(origen="subida_manual"))
    procesamiento.procesar(conn, cfg, doc_id, proveedor_ia, ocr)
    assert uno(conn, "SELECT titular_id FROM documentos WHERE id=?", (doc_id,))["titular_id"] == lucia
