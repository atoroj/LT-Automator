"""Almacenamiento local de expedientes y documentos.

- Los originales se guardan en la carpeta de entrada, en modo exclusivo y como solo lectura.
- Nunca se sobrescribe ni se borra un archivo.
- Las copias de trabajo renombradas viven en las subcarpetas por categoría.
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from .config import CATEGORIAS, Config
from .seguridad import (
    escribir_exclusivo,
    marcar_solo_lectura,
    resolver_relativa,
    ruta_segura,
    ruta_unica,
    sanear_nombre,
    slug,
)


def sha256_bytes(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def sha256_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def relativa(cfg: Config, ruta: Path) -> str:
    return ruta.resolve().relative_to(cfg.dir_datos).as_posix()


def absoluta(cfg: Config, rel: str) -> Path:
    return resolver_relativa(cfg.dir_datos, rel)


def nombre_carpeta_expediente(referencia: str, nombre: str) -> str:
    return sanear_nombre(f"{referencia}_{slug(nombre, 30)}", max_len=60)


COLABORADOR_POR_DEFECTO = "Propio"


def carpeta_nueva_expediente(cfg: Config, referencia: str, nombre: str, colaborador: str) -> str:
    """Ruta (relativa a la carpeta de datos) de un expediente nuevo: 02 - EXPEDIENTES/<colaborador>/<ref_nombre>."""
    base = ruta_segura(cfg.dir_expedientes, sanear_nombre(colaborador or COLABORADOR_POR_DEFECTO, 60),
                       nombre_carpeta_expediente(referencia, nombre))
    return relativa(cfg, base)


def nombre_subcarpeta_entrada(referencia: str, nombre: str) -> str:
    return sanear_nombre(f"{referencia} - {nombre}", max_len=80)


def dir_entrada_expediente(cfg: Config, referencia: str, nombre: str) -> Path:
    """Subcarpeta de 00 - ENTRADA donde se depositan los documentos de un expediente."""
    return ruta_segura(cfg.dir_entrada, nombre_subcarpeta_entrada(referencia, nombre))


def crear_estructura_expediente(cfg: Config, carpeta: str) -> Path:
    base = dir_expediente(cfg, carpeta)
    base.mkdir(parents=True, exist_ok=True)
    ruta_segura(base, cfg["expedientes"]["carpeta_entrada"]).mkdir(exist_ok=True)
    for cat in CATEGORIAS:
        ruta_segura(base, cfg["expedientes"]["subcarpetas"][cat]).mkdir(exist_ok=True)
    return base


def dir_expediente(cfg: Config, carpeta: str) -> Path:
    if "/" in carpeta:  # expedientes actuales: ruta relativa a la carpeta de datos
        return resolver_relativa(cfg.dir_datos, carpeta)
    return ruta_segura(cfg.dir_expedientes_antiguo, carpeta)  # expedientes creados con la versión anterior


def dir_originales(cfg: Config, carpeta: str) -> Path:
    return ruta_segura(dir_expediente(cfg, carpeta), cfg["expedientes"]["carpeta_entrada"])


def dir_categoria(cfg: Config, carpeta: str, categoria: str) -> Path:
    if categoria not in CATEGORIAS:
        categoria = "otros"
    return ruta_segura(dir_expediente(cfg, carpeta), cfg["expedientes"]["subcarpetas"][categoria])


def guardar_original(directorio: Path, nombre_original: str, datos: bytes, sha: str) -> Path:
    """Guarda el original de forma inmutable. Idempotente si ya existe con el mismo contenido."""
    nombre = sanear_nombre(f"{datetime.now():%Y%m%d}_{sha[:10]}_{sanear_nombre(nombre_original)}")
    candidato = ruta_segura(directorio, nombre)
    if candidato.exists() and sha256_archivo(candidato) == sha:
        return candidato  # reintento tras una interrupción: ya estaba guardado
    destino = ruta_unica(directorio, nombre)
    escribir_exclusivo(destino, datos)
    marcar_solo_lectura(destino)
    return destino


def crear_copia_trabajo(origen: Path, directorio: Path, nombre: str) -> Path:
    destino = ruta_unica(directorio, nombre)
    destino.parent.mkdir(parents=True, exist_ok=True)
    # copy2 sobre un destino nuevo; ruta_unica garantiza que no existe.
    shutil.copyfile(origen, destino)
    return destino


def mover_copia_trabajo(actual: Path, directorio: Path, nombre: str) -> Path:
    """Renombra/mueve una copia de trabajo (nunca el original) sin sobrescribir."""
    destino = ruta_unica(directorio, nombre)
    destino.parent.mkdir(parents=True, exist_ok=True)
    actual.rename(destino)
    return destino


def guardar_informe(directorio: Path, nombre: str, datos: bytes) -> Path:
    destino = ruta_unica(directorio, nombre)
    escribir_exclusivo(destino, datos)
    return destino
