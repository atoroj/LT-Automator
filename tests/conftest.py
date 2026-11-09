import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "scripts"))
sys.path.insert(0, str(RAIZ / "tests"))

from hipotecas.config import cargar  # noqa: E402
from hipotecas.db import conectar, inicializar  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    c = cargar(RAIZ / "no_existe.toml", sobrescribir={
        "rutas": {"datos": str(tmp_path / "datos")},
        "ocr": {"habilitado": False},
        "gmail": {"habilitado": False},
    })
    c.asegurar_directorios()
    return c


@pytest.fixture
def conn(cfg):
    c = conectar(cfg.ruta_db)
    inicializar(c)
    yield c
    c.close()


@pytest.fixture(scope="session")
def docs_ficticios(tmp_path_factory):
    import generar_documentos_ficticios as gen

    destino = tmp_path_factory.mktemp("ficticios")
    return {p.name: p for p in gen.generar(destino)}


@pytest.fixture
def cliente_ia():
    from simulador_ia import ClienteIASimulado

    return ClienteIASimulado()


@pytest.fixture
def proveedor_ia(cfg, conn, cliente_ia):
    from hipotecas.extraccion.anthropic_proveedor import ProveedorAnthropic

    return ProveedorAnthropic(cfg, cliente=cliente_ia)


class OCRNo:
    cfg = {}
    motivo = "desactivado en pruebas"

    def disponible(self):
        return False


@pytest.fixture
def ocr():
    return OCRNo()
