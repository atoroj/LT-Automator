"""Script de prueba real (scripts/prueba_real.py), sin red: Gmail falso e IA simulada."""
import tomllib

import prueba_real as pr
from hipotecas.config import RAIZ_PROYECTO, cargar
from hipotecas.db import conectar, inicializar
from hipotecas.gmail.conector import ClienteGmail
from hipotecas.gmail.sincronizador import Sincronizador
from test_gmail import ServicioFalso, _mensaje


def test_config_de_prueba_aislada_y_valida(tmp_path):
    base = {
        "ocr": {"habilitado": False, "tesseract_cmd": r"C:\Program Files\Tesseract-OCR\tesseract.exe"},
        "ia": {"proveedor": "local", "permitir_envio_externo": False, "modelo": "claude-opus-5-5"},
        "expedientes": {"subcarpetas": {"otros": "07_Otros"}},
        "rutas": {"datos": r"D:\datos_de_la_usuaria"},
        "gmail": {"etiqueta": "Hipotecas/Procesar", "incluir_remitentes_de_expedientes": True, "consulta": "has:attachment"},
    }
    datos = pr.generar_config(tmp_path / "prueba", "Hipotecas/Prueba", 8766, 5, base)
    ruta = pr.escribir_config(tmp_path / "prueba", datos)
    leido = tomllib.loads(ruta.read_text(encoding="utf-8"))
    assert leido["ocr"]["tesseract_cmd"] == r"C:\Program Files\Tesseract-OCR\tesseract.exe"  # barras invertidas intactas

    cfg = cargar(ruta)
    assert cfg.dir_datos == (tmp_path / "prueba" / "datos").resolve()  # nunca la carpeta de datos de la usuaria
    assert cfg["servidor"]["puerto"] == 8766 and cfg["carpetas"]["vigilar_entrada"] is False
    assert cfg["ia"]["proveedor"] == "anthropic" and cfg["ia"]["permitir_envio_externo"] is True
    g = cfg["gmail"]
    assert g["etiqueta"] == "Hipotecas/Prueba" and g["incluir_remitentes_de_expedientes"] is False and g["consulta"] == ""
    assert cfg["expedientes"]["subcarpetas"]["laboral"] == "02_Laboral"  # el resto, por defecto


def test_rechaza_carpeta_dentro_del_proyecto(capsys):
    assert pr.main(["--dir", str(RAIZ_PROYECTO / "datos" / "prueba")]) == 2
    assert "dentro del proyecto" in capsys.readouterr().err


def test_informe_sin_datos_personales(tmp_path, docs_ficticios, ocr, monkeypatch, capsys):
    from hipotecas.extraccion.anthropic_proveedor import ProveedorAnthropic
    from simulador_ia import ClienteIASimulado

    cfg = cargar(pr.escribir_config(tmp_path, pr.generar_config(tmp_path, "Hipotecas/Prueba", 8766, 10, {})))
    cfg.asegurar_directorios()
    conn = conectar(cfg.ruta_db)
    inicializar(conn)
    exp = pr.preparar_expediente(conn, cfg, "Prueba real", "lucia.prueba@ejemplo.invalid",
                                 "compra_vivienda_habitual", "Lucía Prueba Ficticia", "asalariado_indefinido")
    assert pr.preparar_expediente(conn, cfg, "Prueba real", "", "compra_vivienda_habitual", None, "otro")["id"] == exp["id"]

    nomina = docs_ficticios["nomina_2026_10.pdf"].read_bytes()
    servicio = ServicioFalso(
        {"a": _mensaje("a", "Lucía <lucia.prueba@ejemplo.invalid>", "Mi nómina", [("nomina_octubre.pdf", nomina)]),
         "b": _mensaje("b", "desconocido@otro.invalid", "Hola", [("x.pdf", nomina)])},
        {"L1": "Hipotecas/Prueba"},
    )
    monkeypatch.setattr("hipotecas.gmail.sincronizador.almacen", lambda c: type("A", (), {"existe": lambda self: True})())
    proveedor = ProveedorAnthropic(cfg, cliente=ClienteIASimulado())
    monkeypatch.setattr("hipotecas.gmail.sincronizador.obtener_proveedor", lambda c: (proveedor, "IA simulada"))
    sync = Sincronizador(cfg, ocr, fabrica_cliente=lambda: ClienteGmail(cfg, servicio))
    assert sync.sincronizar()["procesados"] == 1

    capsys.readouterr()
    res = pr.informe(conn)
    salida = capsys.readouterr().out
    assert res["mensajes"] == {"a": "procesado", "b": "incidencia"}
    nom = next(d for d in res["documentos"] if d["tipo"] == "nomina")
    assert nom["estado"] == "procesado" and nom["campos"]
    assert res["incidencias"].get("correo_sin_expediente") == 1
    # Por defecto no aparecen remitentes, nombres de archivo ni valores
    for dato in ("lucia.prueba", "nomina_octubre", "Lucía", "1.650", "desconocido@"):
        assert dato not in salida

    pr.informe(conn, mostrar_valores=True)
    assert "lucia.prueba@ejemplo.invalid" in capsys.readouterr().out
    conn.close()
