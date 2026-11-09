"""Carpeta de entrada vigilada (00 - ENTRADA), estructura GESTOR HIPOTECARIO y colaboradores."""
import os
import shutil
import time

from hipotecas import almacenamiento as alm
from hipotecas import expedientes as exps
from hipotecas.carpeta_entrada import CARPETA_DUPLICADOS, escanear
from hipotecas.db import todos, uno

FUTURO = time.time() + 3600  # evita esperar los segundos de estabilidad


def _exp(conn, cfg, colaborador="Propio"):
    exp_id = exps.crear_expediente(conn, cfg, "Entrada ficticia", "compra_vivienda_habitual", colaborador=colaborador)
    exps.anadir_titular(conn, exp_id, "Lucía Prueba Ficticia", "asalariado_indefinido", "12345678Z")
    return exps.obtener(conn, exp_id)


def test_estructura_y_subcarpeta_por_expediente(conn, cfg):
    exp = _exp(conn, cfg, colaborador="Bróker Ejemplo")
    assert cfg.dir_entrada.name == "00 - ENTRADA" and cfg.dir_entrada.parent.name == "GESTOR HIPOTECARIO"
    sub = alm.dir_entrada_expediente(cfg, exp["referencia"], exp["nombre"])
    assert sub.is_dir() and sub.name.startswith(exp["referencia"])
    base = alm.dir_expediente(cfg, exp["carpeta"])
    assert base.parent.name == "Bróker Ejemplo" and base.parent.parent == cfg.dir_expedientes
    assert (base / cfg["expedientes"]["carpeta_entrada"]).is_dir()


def test_archivo_en_subcarpeta_se_procesa_y_se_retira(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    exp = _exp(conn, cfg)
    sub = alm.dir_entrada_expediente(cfg, exp["referencia"], exp["nombre"])
    destino = sub / "foto_whatsapp_arras.pdf"
    shutil.copyfile(docs_ficticios["contrato_arras.pdf"], destino)

    c = escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=FUTURO)
    assert c["procesados"] == 1
    assert not destino.exists()  # retirado tras guardarlo y verificarlo
    doc = uno(conn, "SELECT * FROM documentos WHERE expediente_id=?", (exp["id"],))
    assert doc["origen"] == "carpeta" and doc["nombre_original"] == "foto_whatsapp_arras.pdf"
    assert doc["tipo_documento"] == "contrato_arras" and doc["estado"] == "procesado"
    assert alm.sha256_archivo(alm.absoluta(cfg, doc["ruta_original"])) == doc["sha256"]
    assert "05_Vivienda" in doc["ruta_trabajo"]


def test_archivo_suelto_sin_referencia_va_a_incidencias(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    _exp(conn, cfg)
    suelto = cfg.dir_entrada / "documento.pdf"
    shutil.copyfile(docs_ficticios["nota_simple.pdf"], suelto)
    c = escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=FUTURO)
    assert c["incidencias"] == 1 and not suelto.exists()
    inc = uno(conn, "SELECT * FROM incidencias WHERE tipo='correo_sin_expediente'")
    doc = uno(conn, "SELECT * FROM documentos WHERE gmail_mensaje_id=?", (inc["gmail_mensaje_id"],))
    assert doc["expediente_id"] is None and doc["estado"] == "en_bandeja_incidencias"
    assert alm.absoluta(cfg, doc["ruta_original"]).is_relative_to(cfg.dir_incidencias)


def test_archivo_suelto_con_referencia_en_el_nombre(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    exp = _exp(conn, cfg)
    suelto = cfg.dir_entrada / f"{exp['referencia']} nota simple.pdf"
    shutil.copyfile(docs_ficticios["nota_simple.pdf"], suelto)
    assert escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=FUTURO)["procesados"] == 1
    assert uno(conn, "SELECT expediente_id FROM documentos")["expediente_id"] == exp["id"]


def test_archivo_que_se_esta_copiando_espera(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    exp = _exp(conn, cfg)
    sub = alm.dir_entrada_expediente(cfg, exp["referencia"], exp["nombre"])
    f = sub / "recien_copiado.pdf"
    shutil.copyfile(docs_ficticios["nota_simple.pdf"], f)
    c = escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=time.time())  # mtime reciente
    assert c["en_espera"] == 1 and f.exists() and not todos(conn, "SELECT id FROM documentos")


def test_mismo_archivo_dos_veces_no_se_reprocesa(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    exp = _exp(conn, cfg)
    sub = alm.dir_entrada_expediente(cfg, exp["referencia"], exp["nombre"])
    shutil.copyfile(docs_ficticios["nota_simple.pdf"], sub / "nota.pdf")
    escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=FUTURO)
    shutil.copyfile(docs_ficticios["nota_simple.pdf"], sub / "nota otra vez.pdf")
    c = escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=FUTURO)
    assert c["duplicados"] == 1
    assert len(todos(conn, "SELECT id FROM documentos")) == 1
    assert (cfg.dir_incidencias / CARPETA_DUPLICADOS / "nota otra vez.pdf").exists()  # movido, no borrado


def test_formato_no_admitido_y_temporales(conn, cfg, ocr, docs_ficticios, proveedor_ia):
    exp = _exp(conn, cfg)
    sub = alm.dir_entrada_expediente(cfg, exp["referencia"], exp["nombre"])
    (sub / "notas.txt").write_text("formato no admitido", encoding="utf-8")
    (sub / "~$temporal.docx").write_bytes(b"x")
    (sub / "descarga.pdf.crdownload").write_bytes(b"x")
    escanear(conn, cfg, proveedor_ia, ocr, ahora_ts=FUTURO)
    doc = uno(conn, "SELECT * FROM documentos WHERE nombre_original='notas.txt'")
    assert doc["estado"] == "incidencia"  # se conserva el original y queda como incidencia visible
    assert (sub / "~$temporal.docx").exists() and (sub / "descarga.pdf.crdownload").exists()


def test_expedientes_antiguos_siguen_funcionando(conn, cfg):
    """Expedientes creados con la versión anterior (carpeta en datos/expedientes) se siguen leyendo ahí."""
    antiguo = cfg.dir_expedientes_antiguo / "EXP-2025-0001_Antiguo"
    os.makedirs(antiguo)
    assert alm.dir_expediente(cfg, "EXP-2025-0001_Antiguo") == antiguo.resolve()


def test_colaborador_filtra(conn, cfg):
    _exp(conn, cfg, colaborador="Bróker A")
    _exp(conn, cfg, colaborador="Bróker B")
    assert [e["colaborador"] for e in exps.listar(conn, colaborador="Bróker A")] == ["Bróker A"]
    assert set(exps.colaboradores(conn)) == {"Bróker A", "Bróker B"}
