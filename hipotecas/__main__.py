"""Arranque: python -m hipotecas [--sin-navegador] [--config RUTA]"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import webbrowser


def _puerto_ocupado(host: str, puerto: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, puerto)) == 0


def main() -> int:
    p = argparse.ArgumentParser(description="Gestión local de expedientes hipotecarios")
    p.add_argument("--config", help="Ruta a config.toml")
    p.add_argument("--sin-navegador", action="store_true", help="No abrir el navegador al arrancar")
    args = p.parse_args()

    if sys.stdout is None or sys.stderr is None:  # pythonw.exe (arranque automático sin consola)
        nulo = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        sys.stdout = sys.stdout or nulo
        sys.stderr = sys.stderr or nulo

    from .config import cargar

    try:
        cfg = cargar(args.config)
    except ValueError as e:
        print(f"Error de configuración: {e}", file=sys.stderr)
        return 2
    host = "127.0.0.1"  # nunca otra interfaz, aunque la configuración diga 'localhost'
    puerto = int(cfg["servidor"]["puerto"])
    url = f"http://{host}:{puerto}/"
    abrir = cfg["servidor"].get("abrir_navegador", True) and not args.sin_navegador
    if os.environ.get("HIPOTECAS_CONTENEDOR") == "1":
        # Dentro de Docker hay que escuchar en la interfaz del contenedor. docker-compose solo publica
        # el puerto en 127.0.0.1 del servidor; el acceso remoto va por Tailscale (ver DESPLIEGUE.md).
        host, abrir = "0.0.0.0", False

    if host == "127.0.0.1" and _puerto_ocupado(host, puerto):
        print(f"La aplicación ya está en marcha en {url}")
        if abrir:
            webbrowser.open(url)
        return 0

    import uvicorn

    from .web.app import crear_app

    app = crear_app(cfg)
    if abrir:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Aplicación disponible en {url}  (Ctrl+C para detener)")
    print(f"Datos en: {cfg.dir_datos}")
    uvicorn.run(app, host=host, port=puerto, log_level="warning", access_log=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
