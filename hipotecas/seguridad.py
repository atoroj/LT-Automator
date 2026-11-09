"""Protección de rutas de archivo y saneado de nombres."""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_RESERVADOS_WINDOWS = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class RutaNoPermitida(ValueError):
    pass


def sanear_nombre(nombre: str, max_len: int = 120, defecto: str = "documento") -> str:
    """Devuelve un nombre de archivo seguro para Windows, sin separadores ni rutas."""
    nombre = unicodedata.normalize("NFKC", nombre or "")
    # Quedarse solo con el último componente aunque venga con rutas.
    nombre = re.split(r"[\\/]", nombre)[-1]
    nombre = re.sub(r'[\x00-\x1f<>:"|?*]', "_", nombre)
    nombre = nombre.strip().strip(".").strip()
    nombre = re.sub(r"\s+", " ", nombre)
    if not nombre:
        nombre = defecto
    base, ext = os.path.splitext(nombre)
    if base.upper() in _RESERVADOS_WINDOWS:
        base = f"_{base}"
    ext = ext[:10]
    base = base[: max(1, max_len - len(ext))]
    return f"{base}{ext}"


def slug(texto: str, max_len: int = 40) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_")
    return (t[:max_len] or "x")


def ruta_segura(base: Path, *partes: str) -> Path:
    """Une partes a base y garantiza que el resultado queda dentro de base."""
    base = Path(base).resolve()
    destino = base.joinpath(*partes).resolve()
    if destino != base and not destino.is_relative_to(base):
        raise RutaNoPermitida(f"Ruta fuera del directorio permitido: {destino}")
    return destino


def resolver_relativa(base: Path, relativa: str) -> Path:
    """Convierte una ruta relativa almacenada en BD en absoluta, verificando que no escapa."""
    if not relativa or Path(relativa).is_absolute() or ".." in Path(relativa).parts:
        raise RutaNoPermitida("Ruta almacenada no válida")
    return ruta_segura(base, relativa)


def ruta_unica(directorio: Path, nombre: str) -> Path:
    """Devuelve una ruta que no existe todavía (nunca sobrescribe)."""
    nombre = sanear_nombre(nombre)
    candidato = ruta_segura(directorio, nombre)
    base, ext = os.path.splitext(nombre)
    n = 2
    while candidato.exists():
        candidato = ruta_segura(directorio, f"{base}_{n}{ext}")
        n += 1
    return candidato


def escribir_exclusivo(ruta: Path, datos: bytes) -> None:
    """Crea el archivo; falla si ya existe (no sobrescribe nunca)."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "xb") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())


def marcar_solo_lectura(ruta: Path) -> None:
    try:
        os.chmod(ruta, 0o444)
    except OSError:
        pass
