"""Almacenamiento seguro del token OAuth.

En Windows se cifra con DPAPI (CryptProtectData) ligado a la cuenta de usuario de Windows:
solo esa usuaria, en ese equipo, puede descifrarlo. En otros sistemas (solo desarrollo/pruebas)
se guarda con permisos 600.
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

ENTROPIA = b"hipotecas-local-gmail-v1"


if sys.platform == "win32":
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(datos: bytes) -> _BLOB:
        buf = ctypes.create_string_buffer(datos, len(datos))
        return _BLOB(len(datos), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _leer_blob(b: _BLOB) -> bytes:
        datos = ctypes.string_at(b.pbData, b.cbData)
        ctypes.windll.kernel32.LocalFree(b.pbData)
        return datos

    def cifrar(datos: bytes) -> bytes:
        entrada, entropia, salida = _blob(datos), _blob(ENTROPIA), _BLOB()
        CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(entrada), "token", ctypes.byref(entropia), None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(salida)
        ):
            raise OSError("CryptProtectData falló")
        return _leer_blob(salida)

    def descifrar(datos: bytes) -> bytes:
        entrada, entropia, salida = _blob(datos), _blob(ENTROPIA), _BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(entrada), None, ctypes.byref(entropia), None, None, 0x1, ctypes.byref(salida)
        ):
            raise OSError("CryptUnprotectData falló (¿token de otro usuario o equipo?)")
        return _leer_blob(salida)

    PROTECCION = "DPAPI (cifrado con la cuenta de Windows)"
else:  # pragma: no cover - solo para desarrollo fuera de Windows
    def cifrar(datos: bytes) -> bytes:
        return datos

    def descifrar(datos: bytes) -> bytes:
        return datos

    PROTECCION = "archivo con permisos 600 (sin cifrar: solo desarrollo)"


class AlmacenToken:
    def __init__(self, ruta: Path):
        self.ruta = ruta

    def existe(self) -> bool:
        return self.ruta.exists()

    def guardar(self, token_json: str) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.ruta.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(cifrar(token_json.encode("utf-8")))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.ruta)  # el token sí se reemplaza (es un secreto renovable, no un documento)

    def cargar(self) -> str | None:
        if not self.ruta.exists():
            return None
        return descifrar(self.ruta.read_bytes()).decode("utf-8")

    def revocar_local(self) -> None:
        """Elimina el token local (desconectar Gmail). No afecta a ningún documento."""
        if self.ruta.exists():
            self.ruta.unlink()
