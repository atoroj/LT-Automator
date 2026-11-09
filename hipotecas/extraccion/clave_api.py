"""Clave de API del proveedor de IA: variable de entorno o archivo cifrado con DPAPI.

La clave nunca se guarda en config.toml ni en el repositorio, y la interfaz nunca la muestra.
"""
from __future__ import annotations

import os

from ..config import Config
from ..gmail.almacen_token import AlmacenToken

VARIABLE = "ANTHROPIC_API_KEY"


def almacen(cfg: Config) -> AlmacenToken:
    return AlmacenToken(cfg.dir_secretos / "anthropic_api_key.bin")


def obtener(cfg: Config) -> tuple[str | None, str]:
    """Devuelve (clave, origen). Prioridad: variable de entorno, después archivo cifrado."""
    if os.environ.get(VARIABLE):
        return os.environ[VARIABLE], f"variable de entorno {VARIABLE}"
    a = almacen(cfg)
    if a.existe():
        try:
            return a.cargar(), "archivo cifrado (DPAPI)"
        except OSError:
            return None, "archivo cifrado ilegible (¿otro usuario de Windows?)"
    return None, "no configurada"


def guardar(cfg: Config, clave: str) -> None:
    clave = (clave or "").strip()
    if len(clave) < 20 or any(c.isspace() for c in clave):
        raise ValueError("La clave no tiene un formato válido")
    almacen(cfg).guardar(clave)


def eliminar(cfg: Config) -> None:
    almacen(cfg).revocar_local()
