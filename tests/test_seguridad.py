import sys

import pytest

from hipotecas.config import cargar
from hipotecas.seguridad import RutaNoPermitida, escribir_exclusivo, resolver_relativa, ruta_segura, ruta_unica, sanear_nombre


def test_sanear_nombre_elimina_rutas_y_caracteres():
    assert sanear_nombre("..\\..\\Windows\\system32\\evil.pdf") == "evil.pdf"
    assert sanear_nombre("../../etc/passwd") == "passwd"
    assert sanear_nombre('a<b>c:"d|e?f*.pdf') == "a_b_c__d_e_f_.pdf"
    assert sanear_nombre("CON.pdf") == "_CON.pdf"
    assert sanear_nombre("") == "documento"


def test_ruta_segura_bloquea_traversal(tmp_path):
    with pytest.raises(RutaNoPermitida):
        ruta_segura(tmp_path, "..", "fuera.txt")
    with pytest.raises(RutaNoPermitida):
        resolver_relativa(tmp_path, "../fuera.txt")
    with pytest.raises(RutaNoPermitida):
        resolver_relativa(tmp_path, "C:/Windows/win.ini")


def test_no_sobrescribe(tmp_path):
    p = tmp_path / "a.pdf"
    escribir_exclusivo(p, b"1")
    with pytest.raises(FileExistsError):
        escribir_exclusivo(p, b"2")
    assert p.read_bytes() == b"1"
    assert ruta_unica(tmp_path, "a.pdf").name == "a_2.pdf"


def test_config_rechaza_host_no_loopback(tmp_path):
    with pytest.raises(ValueError):
        cargar(tmp_path / "x.toml", sobrescribir={"servidor": {"host": "0.0.0.0"}})
    with pytest.raises(ValueError):
        cargar(tmp_path / "x.toml", sobrescribir={"servidor": {"host": "192.168.1.10"}})
    assert cargar(tmp_path / "x.toml")["servidor"]["host"] == "127.0.0.1"


def test_token_dpapi_ida_y_vuelta(tmp_path):
    from hipotecas.gmail.almacen_token import AlmacenToken

    a = AlmacenToken(tmp_path / "t.bin")
    a.guardar('{"token": "secreto-ficticio"}')
    if sys.platform == "win32":
        assert b"secreto-ficticio" not in (tmp_path / "t.bin").read_bytes()
    assert a.cargar() == '{"token": "secreto-ficticio"}'
