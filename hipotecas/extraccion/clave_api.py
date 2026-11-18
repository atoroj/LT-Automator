"""Clave de API del proveedor de IA: se lee de un secreto, nunca de la interfaz.

Orden de búsqueda:
1. Archivo de secreto. Ruta en la variable ANTHROPIC_API_KEY_FILE o en `[ia] archivo_clave`;
   si no se indica ninguna, /run/secrets/anthropic_api_key (secreto de Docker Compose en el servidor).
2. Variable de entorno ANTHROPIC_API_KEY (cómodo en el PC).

La clave nunca se guarda en config.toml ni en el repositorio, la interfaz no permite introducirla
y nunca se muestra.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..config import Config

VARIABLE = "ANTHROPIC_API_KEY"
VARIABLE_ARCHIVO = "ANTHROPIC_API_KEY_FILE"
SECRETO_DOCKER = Path("/run/secrets/anthropic_api_key")


def ruta_secreto(cfg: Config) -> tuple[Path | None, bool]:
    """Devuelve (ruta, indicada_expresamente). La ruta por defecto solo cuenta si existe."""
    indicada = os.environ.get(VARIABLE_ARCHIVO) or (cfg["ia"].get("archivo_clave") or "").strip()
    if indicada:
        return Path(indicada).expanduser(), True
    return (SECRETO_DOCKER, False) if SECRETO_DOCKER.exists() else (None, False)


def _valida(clave: str) -> bool:
    return len(clave) >= 20 and not any(c.isspace() for c in clave)


def obtener(cfg: Config) -> tuple[str | None, str]:
    """Devuelve (clave, origen). El origen es una descripción sin la clave."""
    ruta, expresa = ruta_secreto(cfg)
    if ruta is not None:
        try:
            clave = ruta.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            clave = None
            if expresa:
                return None, f"no existe el archivo de secreto {ruta}"
        except OSError as e:
            return None, f"no se puede leer el archivo de secreto {ruta} ({type(e).__name__})"
        if clave:
            if not _valida(clave):
                return None, f"el archivo de secreto {ruta} no contiene una clave válida"
            return clave, f"archivo de secreto {ruta}"
        if expresa and os.environ.get(VARIABLE) is None:
            return None, f"el archivo de secreto {ruta} está vacío"
    clave = (os.environ.get(VARIABLE) or "").strip()
    if clave:
        if not _valida(clave):
            return None, f"la variable de entorno {VARIABLE} no contiene una clave válida"
        return clave, f"variable de entorno {VARIABLE}"
    return None, "no configurada"
