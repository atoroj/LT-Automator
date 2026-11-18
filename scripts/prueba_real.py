"""Prueba real del proceso: Gmail → documentos → texto/OCR → clasificación y extracción con IA.

Trabaja en una carpeta de prueba propia, FUERA del proyecto, con su propia configuración, base de
datos y token de Gmail. No toca tu config.toml ni tu carpeta datos/ (solo los lee para copiar el
OCR, el modelo de IA y, si no indicas otro, el credentials.json de Gmail).

Uso (PowerShell, desde la carpeta del proyecto):
    .\\.venv\\Scripts\\python.exe scripts\\prueba_real.py --etiqueta "Hipotecas/Prueba" `
        --expediente "Prueba real" --emails cliente@ejemplo.com

1. En Gmail, pon la etiqueta indicada a los correos de prueba. Gmail se usa en modo solo lectura:
   no se modifica ni se borra ningún correo.
2. Los correos se asocian al expediente de prueba por la referencia en el asunto o por el remitente
   (--emails). Los que no se puedan asociar van a incidencias, como en la aplicación.
3. Al terminar, el informe muestra qué se ha recibido, clasificado y extraído. Por defecto NO muestra
   datos personales (solo tipos, estados y nombres de los campos), así que se puede compartir.
   Con --mostrar-valores enseña los valores y las citas: úsalo solo en tu terminal.
4. Para revisarlo en la interfaz, arranca la aplicación con la configuración de prueba (el script
   muestra el comando). Para empezar de cero, borra la carpeta de prueba.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from hipotecas import catalogo  # noqa: E402
from hipotecas import expedientes as exps  # noqa: E402
from hipotecas.config import RAIZ_PROYECTO, Config, cargar  # noqa: E402
from hipotecas.db import conectar, inicializar, todos, uno  # noqa: E402

SECCIONES_HEREDADAS = ("ocr", "documentos", "expedientes", "ia", "calculos")


def carpeta_por_defecto() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "GestorHipotecario" / "prueba-real"
    return Path.home() / ".local" / "share" / "gestor-hipotecario" / "prueba-real"


# --------------------------------------------------------------------------- configuración

def _valor_toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # una cadena JSON es una cadena básica TOML válida
    if isinstance(v, list):
        return "[" + ", ".join(_valor_toml(x) for x in v) + "]"
    raise TypeError(f"Tipo no admitido en TOML: {type(v).__name__}")


def a_toml(datos: dict, prefijo: str = "") -> str:
    simples = [(k, v) for k, v in datos.items() if not isinstance(v, dict)]
    tablas = [(k, v) for k, v in datos.items() if isinstance(v, dict)]
    lineas = [f"{k} = {_valor_toml(v)}" for k, v in simples]
    for k, v in tablas:
        nombre = f"{prefijo}.{k}" if prefijo else k
        lineas += ["", f"[{nombre}]", a_toml(v, nombre)]
    return "\n".join(lineas).strip("\n")


def generar_config(carpeta: Path, etiqueta: str, puerto: int, max_mensajes: int, base: dict) -> dict:
    """Configuración de prueba: hereda OCR, IA, etc. de la tuya y aísla datos, Gmail y puerto."""
    datos = {s: base[s] for s in SECCIONES_HEREDADAS if isinstance(base.get(s), dict)}
    ia = dict(datos.get("ia", {}))
    ia.update({"proveedor": "anthropic", "permitir_envio_externo": True})
    datos["ia"] = ia
    datos["rutas"] = {"datos": str(carpeta / "datos")}
    datos["servidor"] = {"host": "127.0.0.1", "puerto": puerto, "abrir_navegador": False, "hosts_permitidos": []}
    datos["carpetas"] = {"vigilar_entrada": False}
    datos["gmail"] = {
        "habilitado": True, "etiqueta": etiqueta,
        # Solo los correos con la etiqueta: nada de búsquedas por remitente ni consultas adicionales.
        "incluir_remitentes_de_expedientes": False, "consulta": "",
        "max_mensajes_por_sincronizacion": max_mensajes,
    }
    if isinstance(base.get("gmail"), dict) and base["gmail"].get("patron_referencia"):
        datos["gmail"]["patron_referencia"] = base["gmail"]["patron_referencia"]
    return datos


def escribir_config(carpeta: Path, datos: dict) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "config.toml"
    cabecera = ("# Generado por scripts/prueba_real.py: se reescribe en cada ejecución.\n"
                "# Configuración de PRUEBA, separada de la de la aplicación.\n\n")
    ruta.write_text(cabecera + a_toml(datos) + "\n", encoding="utf-8")
    return ruta


def dentro_del_proyecto(carpeta: Path) -> bool:
    try:
        carpeta.resolve().relative_to(RAIZ_PROYECTO)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- preparación

def preparar_gmail(cfg: Config, cfg_base: Config | None, credenciales: str | None) -> None:
    from hipotecas.gmail.conector import SCOPES, almacen, guardar_credenciales, ruta_credenciales

    if not ruta_credenciales(cfg).exists():
        origen = Path(credenciales) if credenciales else (ruta_credenciales(cfg_base) if cfg_base else None)
        if not origen or not origen.exists():
            raise SystemExit("Falta el credentials.json de Gmail (cliente OAuth «Aplicación de escritorio»). "
                             "Indícalo con --credenciales RUTA.")
        guardar_credenciales(cfg, origen.read_bytes())
        print(f"  Credenciales OAuth copiadas a la carpeta de prueba (desde {origen}).")
    if almacen(cfg).existe():
        print("  Gmail ya autorizado en la carpeta de prueba.")
        return
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("  Se abre el navegador: elige la cuenta de Gmail y acepta el acceso de SOLO LECTURA.")
    flujo = InstalledAppFlow.from_client_secrets_file(str(ruta_credenciales(cfg)), SCOPES)
    creds = flujo.run_local_server(host="127.0.0.1", port=0, open_browser=True,
                                   access_type="offline", prompt="consent", include_granted_scopes="false",
                                   authorization_prompt_message="  Si no se abre, visita: {url}",
                                   success_message="Autorización completada. Puedes cerrar esta pestaña.")
    if not creds.refresh_token:
        raise SystemExit("Google no devolvió un token de actualización; revoca el acceso y repite.")
    almacen(cfg).guardar(creds.to_json())
    print("  Gmail autorizado (token cifrado en la carpeta de prueba).")


def preparar_expediente(conn, cfg: Config, nombre: str, emails: str, operacion: str,
                        titular: str | None, perfil: str) -> dict:
    exp = uno(conn, "SELECT * FROM expedientes WHERE nombre=? ORDER BY id LIMIT 1", (nombre,))
    if exp is None:
        exp_id = exps.crear_expediente(conn, cfg, nombre, operacion, emails)
        if titular:
            exps.anadir_titular(conn, exp_id, titular, perfil)
        exp = exps.obtener(conn, exp_id)
        print(f"  Expediente de prueba creado: {exp['referencia']}")
    else:
        if emails and emails != exp["emails_contacto"]:
            exps.actualizar_expediente(conn, exp["id"], emails_contacto=emails)
            exp = exps.obtener(conn, exp["id"])
        print(f"  Expediente de prueba existente: {exp['referencia']}")
    return dict(exp)


# --------------------------------------------------------------------------- informe

def informe(conn, mostrar_valores: bool = False) -> dict:
    """Imprime el resultado y devuelve un resumen (para pruebas)."""
    res: dict = {"mensajes": {}, "documentos": [], "incidencias": {}}
    print("\n== Correos ==")
    for m in todos(conn, "SELECT m.*, e.referencia FROM mensajes m LEFT JOIN expedientes e ON e.id=m.expediente_id "
                         "WHERE m.origen='gmail' ORDER BY m.procesado_en, m.rowid"):
        res["mensajes"][m["mensaje_id"]] = m["estado"]
        n = uno(conn, "SELECT COUNT(*) n FROM documentos WHERE gmail_mensaje_id=?", (m["mensaje_id"],))["n"]
        destino = m["referencia"] or "sin expediente"
        linea = f"  {m['mensaje_id']}: {m['estado']} → {destino} · adjuntos guardados: {n}"
        if mostrar_valores:
            linea += f"\n      de: {m['remitente']} · asunto: {m['asunto']}"
        if m["motivo"]:
            linea += f"\n      motivo: {m['motivo']}"
        print(linea)
    if not res["mensajes"]:
        print("  Ninguno. ¿Tienen la etiqueta los correos? ¿Llevan adjuntos?")

    print("\n== Documentos ==")
    for d in todos(conn, "SELECT * FROM documentos WHERE origen='gmail' ORDER BY id"):
        campos = todos(conn, "SELECT clave, valor, estado, pagina, evidencia FROM campos WHERE documento_id=? ORDER BY clave, id",
                       (d["id"],))
        res["documentos"].append({"id": d["id"], "tipo": d["tipo_documento"], "estado": d["estado"],
                                  "categoria": d["categoria"], "campos": [c["clave"] for c in campos]})
        print(f"  #{d['id']} {catalogo.etiqueta_tipo_doc(d['tipo_documento'])} [{d['tipo_documento']}] · "
              f"carpeta: {catalogo.CATEGORIAS.get(d['categoria'], d['categoria'])} · estado: {d['estado']}")
        print(f"      texto: {d['metodo_texto'] or '-'} · páginas: {d['num_paginas'] or '-'} · "
              f"fecha del documento: {'sí' if d['fecha_documento'] else 'no'} · titular asignado: {'sí' if d['titular_id'] else 'no'}")
        if mostrar_valores:
            print(f"      original: {d['nombre_original']}")
            print(f"      copia de trabajo: {d['ruta_trabajo'] or '-'}")
        if not campos:
            print("      datos extraídos: ninguno")
        for c in campos:
            if mostrar_valores:
                print(f"      - {catalogo.etiqueta_campo(c['clave'])}: {c['valor']}  ({c['estado']}, pág. {c['pagina']})"
                      f"\n          cita: «{c['evidencia']}»")
            else:
                print(f"      - {catalogo.etiqueta_campo(c['clave'])} ({c['estado']})")

    print("\n== Incidencias abiertas ==")
    for i in todos(conn, "SELECT tipo, COUNT(*) n FROM incidencias WHERE estado='abierta' GROUP BY tipo ORDER BY tipo"):
        res["incidencias"][i["tipo"]] = i["n"]
        print(f"  {i['tipo']}: {i['n']}")
    if mostrar_valores:
        for i in todos(conn, "SELECT id, tipo, descripcion FROM incidencias WHERE estado='abierta' ORDER BY id"):
            print(f"    #{i['id']} {i['tipo']}: {i['descripcion']}")
    if not res["incidencias"]:
        print("  Ninguna.")

    env = uno(conn, "SELECT SUM(tipo='extraccion_ia_externa') e, SUM(tipo='extraccion_ia_cache') c FROM actividad")
    print(f"\n== IA == envíos: {env['e'] or 0} · reutilizados de la caché (sin envío): {env['c'] or 0}")
    return res


# --------------------------------------------------------------------------- principal

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Prueba real de Gmail + extracción con IA en una carpeta aislada")
    p.add_argument("--dir", type=Path, default=carpeta_por_defecto(), help="Carpeta de prueba (fuera del proyecto)")
    p.add_argument("--etiqueta", default="Hipotecas/Prueba", help="Etiqueta de Gmail de los correos de prueba")
    p.add_argument("--expediente", help="Nombre del expediente de prueba (se crea si no existe)")
    p.add_argument("--emails", default="", help="Correos del cliente para asociar por remitente (separados por comas)")
    p.add_argument("--operacion", default="compra_vivienda_habitual", choices=sorted(catalogo.TIPOS_OPERACION))
    p.add_argument("--titular", help="Nombre del titular (opcional; con uno solo, los documentos se le asignan)")
    p.add_argument("--perfil", default="asalariado_indefinido", choices=sorted(catalogo.PERFILES_LABORALES))
    p.add_argument("--credenciales", help="credentials.json de Gmail (por defecto, el de tu carpeta de datos)")
    p.add_argument("--config-base", type=Path, default=RAIZ_PROYECTO / "config.toml",
                   help="Tu configuración, de la que se copian OCR, IA y documentos (solo lectura)")
    p.add_argument("--max-mensajes", type=int, default=10, help="Máximo de correos por ejecución")
    p.add_argument("--puerto", type=int, default=8766, help="Puerto para revisar la prueba en la interfaz")
    p.add_argument("--mostrar-valores", action="store_true",
                   help="Muestra remitentes, nombres de archivo, valores y citas (datos personales)")
    args = p.parse_args(argv)

    carpeta = args.dir.expanduser().resolve()
    if dentro_del_proyecto(carpeta):
        print("La carpeta de prueba no puede estar dentro del proyecto (evita subir datos reales a git).", file=sys.stderr)
        return 2

    print(f"Carpeta de prueba: {carpeta}")
    base, cfg_base = {}, None
    if args.config_base.exists():
        with open(args.config_base, "rb") as f:
            base = tomllib.load(f)
        cfg_base = cargar(args.config_base)
        print(f"  Ajustes de OCR, IA y documentos copiados de {args.config_base}")
    ruta_cfg = escribir_config(carpeta, generar_config(carpeta, args.etiqueta, args.puerto, args.max_mensajes, base))
    cfg = cargar(ruta_cfg)
    cfg.asegurar_directorios()
    conn = conectar(cfg.ruta_db)
    inicializar(conn)

    from hipotecas.documentos.texto import MotorOCR
    from hipotecas.extraccion.servicio import estado_ia, obtener_proveedor

    print("\n== Comprobaciones ==")
    est = estado_ia(cfg)
    print(f"  Clave de IA: {'configurada' if est['clave'] else 'FALTA'} ({est['origen_clave']})")
    proveedor, desc = obtener_proveedor(cfg)
    print(f"  {desc}")
    if proveedor is None:
        print("Sin IA no se puede probar la extracción. Configura la clave como secreto (README, «Activar la IA»).",
              file=sys.stderr)
        return 2
    ocr = MotorOCR(cfg["ocr"])
    print(f"  OCR: {'disponible' if ocr.disponible() else 'no disponible'} ({ocr.motivo})")
    preparar_gmail(cfg, cfg_base, args.credenciales)
    if args.expediente:
        exp = preparar_expediente(conn, cfg, args.expediente, args.emails, args.operacion, args.titular, args.perfil)
        print(f"  Para asociar por asunto, incluye «{exp['referencia']}» en el asunto del correo.")
    elif not uno(conn, "SELECT 1 FROM expedientes LIMIT 1"):
        print("  Aviso: no hay expediente de prueba (--expediente). Los correos irán a incidencias y sus documentos\n"
              "  no se clasificarán hasta asignarlos a un expediente.")

    from hipotecas.gmail.sincronizador import Sincronizador

    print(f"\n== Leyendo Gmail (etiqueta «{args.etiqueta}») y procesando... ==")
    sync = Sincronizador(cfg, ocr)
    cont = sync.sincronizar()
    print(f"  Resultado: {sync.estado.ultimo_resultado}" + (f" — {sync.estado.ultimo_error}" if sync.estado.ultimo_error else ""))
    print("  " + ", ".join(f"{k}: {v}" for k, v in cont.items()))

    informe(conn, args.mostrar_valores)
    conn.close()
    print("\nPara revisarlo en la interfaz:")
    print(f'  .\\.venv\\Scripts\\python.exe -m hipotecas --config "{ruta_cfg}"   →  http://127.0.0.1:{args.puerto}/')
    print("Volver a ejecutar el script solo procesa los correos nuevos (los ya leídos no se duplican).")
    return 0 if sync.estado.ultimo_resultado == "Correcta" else 1


if __name__ == "__main__":
    raise SystemExit(main())
