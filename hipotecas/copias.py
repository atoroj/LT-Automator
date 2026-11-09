"""Copia de seguridad diaria de la base de datos en `datos/copias`.

En el servidor, el contenedor de sincronización sube esa carpeta a Drive. Junto con la copia de
los expedientes que ya hay en Drive, basta para reconstruir la aplicación en otro servidor u ordenador.
Solo se eliminan copias antiguas de la propia base de datos (nunca documentos).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from .config import Config

log = logging.getLogger("hipotecas.copias")


def copia_diaria_bd(cfg: Config, hoy: date | None = None) -> bool:
    """Crea la copia del día si no existe. Devuelve True si se ha creado."""
    hoy = hoy or date.today()
    if not cfg.ruta_db.exists():
        return False
    directorio = cfg.dir_datos / "copias"
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / f"hipotecas_{hoy:%Y%m%d}.db"
    if destino.exists():
        return False
    tmp = destino.with_suffix(".tmp")
    origen = sqlite3.connect(cfg.ruta_db)
    try:
        copia = sqlite3.connect(tmp)
        with copia:
            origen.backup(copia)  # copia coherente aunque la aplicación esté escribiendo
        copia.close()
    finally:
        origen.close()
    tmp.replace(destino)
    limite = hoy - timedelta(days=int(cfg.datos.get("copias", {}).get("dias_conservar", 30)))
    for f in directorio.glob("hipotecas_*.db"):
        try:
            if date(int(f.stem[10:14]), int(f.stem[14:16]), int(f.stem[16:18])) < limite:
                f.unlink()
        except ValueError:
            continue
    log.info("Copia de la base de datos creada")
    return True
