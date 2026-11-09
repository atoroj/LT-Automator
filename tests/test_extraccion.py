"""Extracción con IA (cliente simulado: sin red ni clave) y utilidades de normalización."""
from decimal import Decimal

import httpx

from hipotecas.documentos.texto import MotorOCR, detectar_formato, extraer_texto
from hipotecas.extraccion.anthropic_proveedor import ProveedorAnthropic, seudonimizar
from hipotecas.extraccion.base import dni_valido, normalizar_valor, parsear_fecha, parsear_importe
from simulador_ia import ClienteIASimulado


def _texto(ruta):
    datos = ruta.read_bytes()
    res = extraer_texto(datos, detectar_formato(datos[:16]), 40, MotorOCR({"habilitado": False}))
    return [(p, t) for p, t, _ in res.paginas]


def test_parseo_importes_y_fechas():
    assert parsear_importe("LÍQUIDO: 2.150,40 €") == Decimal("2150.40")
    assert parsear_importe("240.000,00") == Decimal("240000.00")
    assert parsear_importe("8.430") == Decimal("8430")
    assert parsear_fecha("hasta 15/03/2031").isoformat() == "2031-03-15"
    assert parsear_fecha("a 3 de febrero de 2026").isoformat() == "2026-02-03"
    assert normalizar_valor("importe", "180.000,00") == "180000.00"
    assert normalizar_valor("fecha", "basura") is None


def test_dni_letra_control():
    assert dni_valido("12345678Z")
    assert not dni_valido("12345678A")
    assert dni_valido("X1234567L")


def test_ia_clasifica_y_extrae_nomina(proveedor_ia, docs_ficticios):
    nomina = next(p for n, p in sorted(docs_ficticios.items()) if n.startswith("nomina"))
    r = proveedor_ia.extraer(None, _texto(nomina))
    assert not r.fallo and r.tipo_documento == "nomina"
    v = {c.clave: c for c in r.candidatos}
    assert v["nomina_bruto"].valor == "2850.00"
    assert v["nomina_liquido"].valor in ("2150.40", "2148.90", "2152.10")
    assert v["nomina_liquido"].pagina == 1 and "PERCIBIR" in v["nomina_liquido"].evidencia.upper()
    assert v["nomina_liquido"].origen == "extraccion_ia"
    assert v["fecha_antiguedad"].valor == "2019-03-01"
    assert v["_fecha_documento"].valor.endswith(("-30", "-31"))  # fin del periodo, no el inicio
    assert r.traza["enviado"] and r.traza["caracteres_enviados"] > 0


def test_ia_dni_y_arras(proveedor_ia, docs_ficticios):
    r = proveedor_ia.extraer(None, _texto(docs_ficticios["DNI_Lucia_Prueba.pdf"]))
    v = {c.clave: c.valor for c in r.candidatos}
    assert r.tipo_documento == "dni" and v["dni_nie"] == "12345678Z" and v["fecha_nacimiento"] == "1988-05-14"
    r = proveedor_ia.extraer(None, _texto(docs_ficticios["contrato_arras.pdf"]))
    v = {c.clave: c.valor for c in r.candidatos}
    assert r.tipo_documento == "contrato_arras" and v["precio_compra"] == "240000.00" and v["importe_arras"] == "12000.00"


def test_ia_descarta_datos_no_verificables(cfg, conn):
    p = ProveedorAnthropic(cfg, cliente=ClienteIASimulado(inventar=True))
    r = p.extraer(None, [(1, "RECIBO INDIVIDUAL DE SALARIOS\nLÍQUIDO TOTAL A PERCIBIR: 2.150,40 €")])
    valores = [(c.clave, c.valor) for c in r.candidatos]
    assert ("nomina_liquido", "2150.40") in valores
    assert ("precio_compra", "999999.00") not in valores    # cita que no está en el texto
    assert ("nomina_liquido", "9999.00") not in valores     # número que no figura en la cita
    assert any("descartados" in a for a in r.avisos)


def test_ia_respeta_tipo_fijado_y_no_envia_metadatos(cfg, conn, cliente_ia, proveedor_ia):
    r = proveedor_ia.extraer("extracto_bancario", [(1, "Documento cualquiera\nSALDO FINAL: 1.000,00")])
    assert r.tipo_documento is None  # no reclasifica
    enviado = cliente_ia.llamadas[-1]
    assert "fijado por la usuaria: extracto_bancario" in enviado["messages"][0]["content"]
    assert enviado["model"] == "claude-opus-5-5" and enviado["fallbacks"] == "default"
    assert enviado["output_config"]["format"]["type"] == "json_schema"


def test_ia_cache_no_reenvia(proveedor_ia, cliente_ia, docs_ficticios):
    paginas = _texto(docs_ficticios["contrato_arras.pdf"])
    r1 = proveedor_ia.extraer(None, paginas)
    r2 = proveedor_ia.extraer(None, paginas)
    assert len(cliente_ia.llamadas) == 1
    assert r2.traza["cache"] and not r2.traza["enviado"] and r2.traza["caracteres_enviados"] == 0
    assert [c.valor for c in r1.candidatos] == [c.valor for c in r2.candidatos]


def test_seudonimizacion():
    t = seudonimizar("IBAN: ES91 2100 0418 4502 0005 1332\nTel. 612 345 678\ncorreo lucia@ejemplo.invalid\nLÍQUIDO: 2.150,40\nDNI 12345678Z")
    assert "[IBAN]" in t and "[TELEFONO]" in t and "[EMAIL]" in t
    assert "2.150,40" in t and "12345678Z" in t
    assert "2100" not in t and "612" not in t and "lucia@" not in t


def test_ia_texto_enviado_va_seudonimizado(proveedor_ia, cliente_ia):
    proveedor_ia.extraer(None, [(1, "EXTRACTO DE MOVIMIENTOS\nIBAN: ES91 2100 0418 4502 0005 1332\nSALDO FINAL: 10,00")])
    contenido = cliente_ia.llamadas[-1]["messages"][0]["content"]
    assert "2100 0418" not in contenido and "[IBAN]" in contenido


def test_ia_fallo_de_conexion(cfg, conn):
    import anthropic

    err = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.invalid"))
    r = ProveedorAnthropic(cfg, cliente=ClienteIASimulado(error=err)).extraer(None, [(1, "texto")])
    assert r.fallo and not r.candidatos and any("conexión" in a for a in r.avisos)


def test_ia_desactivada_por_defecto(cfg, monkeypatch):
    from hipotecas.extraccion.servicio import obtener_proveedor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p, desc = obtener_proveedor(cfg)
    assert p is None and "permitir_envio_externo" in desc
    cfg.datos["ia"]["permitir_envio_externo"] = True
    p, desc = obtener_proveedor(cfg)
    assert p is None and "clave" in desc.lower()


def test_clave_api_cifrada(cfg, monkeypatch):
    import sys

    from hipotecas.extraccion import clave_api

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    clave_api.guardar(cfg, "sk-ant-clave-ficticia-de-prueba-0000000000")
    clave, origen = clave_api.obtener(cfg)
    assert clave == "sk-ant-clave-ficticia-de-prueba-0000000000" and "DPAPI" in origen
    if sys.platform == "win32":
        assert b"clave-ficticia" not in clave_api.almacen(cfg).ruta.read_bytes()
    clave_api.eliminar(cfg)
    assert clave_api.obtener(cfg)[0] is None


def test_config_antigua_local_no_rompe(cfg):
    from hipotecas.extraccion.servicio import obtener_proveedor

    cfg.datos["ia"]["proveedor"] = "local"
    p, desc = obtener_proveedor(cfg)
    assert p is None and "config.example.toml" in desc
