"""Selección del proveedor de IA para clasificar y extraer datos.

La extracción se hace exclusivamente con IA. Si la IA no está habilitada o no está disponible,
devuelve None: los documentos se guardan igualmente y quedan «pendientes de análisis»
(se pueden clasificar y completar a mano, o analizar más tarde). Nunca se adivina nada.
"""
from __future__ import annotations

import logging

from ..config import Config
from .base import ProveedorExtraccion

log = logging.getLogger("hipotecas.extraccion")


def estado_ia(cfg: Config) -> dict:
    """Resumen de la configuración de IA para mostrar en la interfaz."""
    from . import clave_api

    ia = cfg["ia"]
    clave, origen = clave_api.obtener(cfg)
    return {
        "proveedor": ia.get("proveedor", "anthropic"),
        "permitido": bool(ia.get("permitir_envio_externo", False)),
        "clave": bool(clave),
        "origen_clave": origen,
        "modelo": ia.get("modelo"),
        "esfuerzo": ia.get("esfuerzo"),
        "seudonimizar": bool(ia.get("seudonimizar", True)),
        "max_caracteres": ia.get("max_caracteres_texto"),
    }


def obtener_proveedor(cfg: Config) -> tuple[ProveedorExtraccion | None, str]:
    """Devuelve (proveedor o None, descripción del estado)."""
    ia = cfg["ia"]
    nombre = ia.get("proveedor", "anthropic")
    if nombre == "local":  # configuración anterior: la extracción por reglas ya no existe
        return None, ('IA desactivada: config.toml tiene proveedor = "local". Cambia el bloque [ia] '
                      "según config.example.toml para usar la IA")
    if nombre != "anthropic":
        return None, f"Proveedor de IA desconocido: '{nombre}'"
    if not ia.get("permitir_envio_externo", False):
        return None, "IA desactivada (falta permitir_envio_externo = true en config.toml): análisis manual"
    try:
        from .anthropic_proveedor import ProveedorAnthropic

        p = ProveedorAnthropic(cfg)
    except RuntimeError as e:
        log.warning("Proveedor de IA no disponible: %s", e)
        return None, f"IA no disponible: {e}"
    desc = f"IA: Anthropic ({p.modelo}). Se envía el texto de los documentos"
    if ia.get("seudonimizar", True):
        desc += " (IBAN, correos y teléfonos ocultados)"
    return p, desc
