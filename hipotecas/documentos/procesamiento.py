"""Registro y procesamiento de documentos.

Flujo por documento:
1. registrar(): guarda el original inmutable, calcula SHA-256, detecta duplicados y registra procedencia.
2. procesar(): valida formato y extrae texto (PDF digital u OCR local).
3. analizar(): la IA clasifica y extrae datos (pendientes de confirmar); se asigna titular si es
   inequívoco y se crea la copia de trabajo renombrada. Sin IA, queda «pendiente de análisis».
Cualquier problema se convierte en una incidencia visible; nunca se borra nada.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
from dataclasses import dataclass

from .. import almacenamiento as alm
from ..actividad import registrar as registrar_actividad
from ..catalogo import CAMPOS, TIPOS_DOCUMENTO, categoria_de
from ..config import Config
from ..datos import guardar_candidatos
from ..db import ahora, todos, transaccion, uno
from ..extraccion.base import Candidato, ProveedorExtraccion, buscar_documentos_identidad
from ..incidencias import crear_incidencia
from ..seguridad import sanear_nombre, slug
from .texto import EXTENSION_FORMATO, ErrorLectura, MotorOCR, detectar_formato, extraer_texto, normalizar

log = logging.getLogger("hipotecas.documentos")


@dataclass
class Procedencia:
    origen: str                       # gmail | eml | subida_manual
    mensaje_id: str | None = None
    parte_id: str | None = None
    remitente: str | None = None
    asunto: str | None = None
    fecha_correo: str | None = None


def registrar(
    conn: sqlite3.Connection,
    cfg: Config,
    expediente: sqlite3.Row | None,
    nombre_original: str,
    datos: bytes,
    proc: Procedencia,
) -> tuple[int, bool]:
    """Registra un archivo recibido. Devuelve (documento_id, es_nuevo).

    Si ya se registró ese mismo adjunto de ese mismo mensaje, devuelve el existente (idempotente).
    """
    if proc.mensaje_id and proc.parte_id:
        previo = uno(
            conn, "SELECT id FROM documentos WHERE gmail_mensaje_id=? AND parte_id=?", (proc.mensaje_id, proc.parte_id)
        )
        if previo:
            return previo["id"], False

    nombre_original = sanear_nombre(nombre_original or "adjunto")
    ext = os.path.splitext(nombre_original)[1].lower().lstrip(".")
    sha = alm.sha256_bytes(datos)
    ts = ahora()

    duplicado_de = None
    if expediente is not None:
        previo = uno(
            conn,
            "SELECT id, ruta_original FROM documentos WHERE expediente_id=? AND sha256=? AND duplicado_de IS NULL "
            "ORDER BY id LIMIT 1",
            (expediente["id"], sha),
        )
        if previo:
            duplicado_de = previo["id"]

    if duplicado_de:
        # Contenido idéntico ya guardado: no se crea una segunda copia; se registra la procedencia.
        ruta_rel = uno(conn, "SELECT ruta_original FROM documentos WHERE id=?", (duplicado_de,))["ruta_original"]
        estado = "duplicado"
    else:
        directorio = (
            alm.dir_originales(cfg, expediente["carpeta"]) if expediente is not None
            else alm.ruta_segura(cfg.dir_incidencias, sanear_nombre(slug(proc.mensaje_id or "manual", 60)))
        )
        ruta = alm.guardar_original(directorio, nombre_original, datos, sha)
        ruta_rel = alm.relativa(cfg, ruta)
        estado = "recibido" if expediente is not None else "en_bandeja_incidencias"

    exp_id = expediente["id"] if expediente is not None else None
    with transaccion(conn):
        cur = conn.execute(
            "INSERT INTO documentos(expediente_id, nombre_original, ruta_original, sha256, tamano, extension, estado, origen, "
            "gmail_mensaje_id, parte_id, remitente, asunto, fecha_correo, fecha_recepcion, duplicado_de, creado, actualizado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (exp_id, nombre_original, ruta_rel, sha, len(datos), ext, estado, proc.origen, proc.mensaje_id,
             proc.parte_id, proc.remitente, proc.asunto, proc.fecha_correo, ts, duplicado_de, ts, ts),
        )
        doc_id = cur.lastrowid
        registrar_actividad(
            conn, "documento_recibido",
            f"origen={proc.origen}" + (f", duplicado de #{duplicado_de}" if duplicado_de else ""),
            exp_id, doc_id,
        )
        if exp_id is not None and not duplicado_de:
            otro = uno(
                conn,
                "SELECT d.id, e.referencia FROM documentos d JOIN expedientes e ON e.id=d.expediente_id "
                "WHERE d.sha256=? AND d.expediente_id!=? LIMIT 1",
                (sha, exp_id),
            )
            if otro:
                crear_incidencia(
                    conn, "documento_en_otro_expediente",
                    f"El documento #{doc_id} es idéntico al #{otro['id']} del expediente {otro['referencia']}. "
                    "Comprueba que no se ha enviado a un expediente equivocado.",
                    exp_id, doc_id,
                )
    return doc_id, True


def _nombre_trabajo(doc: sqlite3.Row, titular: sqlite3.Row | None) -> str:
    tipo = doc["tipo_documento"].upper()
    quien = slug(titular["nombre"], 25) if titular else "SIN_TITULAR"
    fecha = (doc["fecha_documento"] or "")[:7].replace("-", "") or "SINFECHA"
    return sanear_nombre(f"{tipo}_{quien}_{fecha}_doc{doc['id']}.{doc['extension'] or 'bin'}")


def actualizar_copia_trabajo(conn: sqlite3.Connection, cfg: Config, doc_id: int) -> None:
    """Crea o recoloca la copia de trabajo según la clasificación y titular actuales."""
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if doc is None or doc["expediente_id"] is None or doc["estado"] == "duplicado":
        return
    exp = uno(conn, "SELECT * FROM expedientes WHERE id=?", (doc["expediente_id"],))
    titular = uno(conn, "SELECT * FROM titulares WHERE id=?", (doc["titular_id"],)) if doc["titular_id"] else None
    nombre = _nombre_trabajo(doc, titular)
    destino_dir = alm.dir_categoria(cfg, exp["carpeta"], doc["categoria"])
    if doc["ruta_trabajo"]:
        actual = alm.absoluta(cfg, doc["ruta_trabajo"])
        if actual.exists():
            if actual.parent == destino_dir and actual.name == nombre:
                return
            nueva = alm.mover_copia_trabajo(actual, destino_dir, nombre)
        else:
            nueva = alm.crear_copia_trabajo(alm.absoluta(cfg, doc["ruta_original"]), destino_dir, nombre)
    else:
        nueva = alm.crear_copia_trabajo(alm.absoluta(cfg, doc["ruta_original"]), destino_dir, nombre)
    conn.execute("UPDATE documentos SET ruta_trabajo=?, actualizado=? WHERE id=?", (alm.relativa(cfg, nueva), ahora(), doc_id))
    registrar_actividad(conn, "copia_trabajo", nueva.name, doc["expediente_id"], doc_id)


def _tokens(nombre: str) -> set[str]:
    return {t for t in normalizar(nombre).replace(",", " ").split() if len(t) > 2}


def _asignar_titular(conn, doc, texto: str, candidatos: list[Candidato]) -> int | None:
    """Asigna titular solo si es inequívoco (único titular, DNI/NIE o nombre completo coincidente)."""
    tits = todos(conn, "SELECT * FROM titulares WHERE expediente_id=?", (doc["expediente_id"],))
    if len(tits) == 1:
        return tits[0]["id"]
    ids = set(buscar_documentos_identidad(texto)) | {c.valor for c in candidatos if c.clave == "dni_nie"}
    coincidentes = [t for t in tits if t["dni_nie"] and t["dni_nie"] in ids]
    if len(coincidentes) == 1:
        return coincidentes[0]["id"]
    nombres = [_tokens(c.valor) for c in candidatos if c.clave in ("nombre_completo", "nombre_en_documento")]
    coincidentes = [t for t in tits if len(_tokens(t["nombre"])) >= 2 and any(_tokens(t["nombre"]) <= n for n in nombres)]
    if len(coincidentes) == 1:
        return coincidentes[0]["id"]
    return None  # dudoso: se deja sin asignar para revisión manual


def procesar(
    conn: sqlite3.Connection, cfg: Config, doc_id: int, proveedor: ProveedorExtraccion | None, ocr: MotorOCR
) -> None:
    """Valida el archivo, extrae su texto (PDF digital u OCR local) y lo analiza con IA."""
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if doc is None or doc["expediente_id"] is None or doc["estado"] in ("duplicado", "descartado"):
        return
    exp_id = doc["expediente_id"]
    try:
        ruta = alm.absoluta(cfg, doc["ruta_original"])
        datos = ruta.read_bytes()
        cfg_docs = cfg["documentos"]
        if len(datos) > cfg_docs["tamano_maximo_mb"] * 1024 * 1024:
            _marcar_incidencia(conn, doc, "tamano_excesivo", f"Supera {cfg_docs['tamano_maximo_mb']} MB")
            return
        formato = detectar_formato(datos[:16])
        ext = doc["extension"]
        if ext not in cfg_docs["extensiones_admitidas"] or formato is None or EXTENSION_FORMATO.get(ext) != formato:
            detalle = f"extensión '.{ext}'" + (f", contenido {formato}" if formato else ", contenido no reconocido")
            _marcar_incidencia(conn, doc, "formato_no_admitido",
                               f"Formato no admitido ({detalle}). Formatos admitidos: PDF e imágenes JPG/PNG/TIFF.")
            return
        try:
            res = extraer_texto(datos, formato, cfg_docs["min_caracteres_pagina"], ocr)
        except ErrorLectura as e:
            _marcar_incidencia(conn, doc, "documento_ilegible", str(e))
            return

        with transaccion(conn):
            conn.execute("DELETE FROM paginas_texto WHERE documento_id=?", (doc_id,))
            conn.executemany(
                "INSERT INTO paginas_texto(documento_id, pagina, texto, metodo) VALUES (?,?,?,?)",
                [(doc_id, p, t, m) for p, t, m in res.paginas],
            )
            conn.execute(
                "UPDATE documentos SET num_paginas=?, metodo_texto=?, formato=?, estado='pendiente_analisis', "
                "actualizado=? WHERE id=?",
                (res.num_paginas, res.metodo, formato, ahora(), doc_id),
            )
            if res.total_caracteres == 0:
                crear_incidencia(
                    conn, "requiere_ocr",
                    "No se ha podido leer texto. " + "; ".join(res.avisos) +
                    ". Clasifícalo y anota sus datos manualmente, o instala Tesseract y pulsa Reprocesar.",
                    exp_id, doc_id,
                )
            elif res.avisos:
                crear_incidencia(conn, "requiere_ocr", "; ".join(res.avisos), exp_id, doc_id)
        analizar(conn, cfg, doc_id, proveedor)
    except Exception as e:  # noqa: BLE001 - cualquier fallo se convierte en incidencia visible
        log.exception("Error procesando documento %s", doc_id)
        crear_incidencia(conn, "error_proceso", f"{type(e).__name__}: {str(e)[:300]}", exp_id, doc_id)
        conn.execute("UPDATE documentos SET estado='incidencia', actualizado=? WHERE id=?", (ahora(), doc_id))


def analizar(conn: sqlite3.Connection, cfg: Config, doc_id: int, proveedor: ProveedorExtraccion | None) -> bool:
    """Clasifica (salvo clasificación manual) y extrae datos con IA a partir del texto guardado.

    Devuelve True si el análisis se completó. Si no hay IA disponible o falla, el documento queda
    «pendiente de análisis»: se puede clasificar a mano o volver a analizar más tarde.
    """
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if doc is None or doc["expediente_id"] is None or doc["estado"] in ("duplicado", "descartado", "incidencia"):
        return False
    exp_id = doc["expediente_id"]
    paginas = [(r["pagina"], r["texto"]) for r in texto_documento(conn, doc_id)]
    hay_texto = any(t.strip() for _, t in paginas)
    if not hay_texto or proveedor is None:
        with transaccion(conn):
            if doc["estado"] in ("recibido", "procesado"):
                conn.execute("UPDATE documentos SET estado='pendiente_analisis', actualizado=? WHERE id=?", (ahora(), doc_id))
            if not doc["titular_manual"] and not doc["titular_id"]:
                # Solo asignaciones inequívocas: titular único o DNI/NIE registrado presente en el texto
                titular = _asignar_titular(conn, doc, "\n".join(t for _, t in paginas), [])
                if titular:
                    conn.execute("UPDATE documentos SET titular_id=? WHERE id=?", (titular, doc_id))
            if hay_texto:
                registrar_actividad(conn, "analisis_pendiente", "IA no configurada: clasificación y datos manuales",
                                    exp_id, doc_id)
            actualizar_copia_trabajo(conn, cfg, doc_id)
        return False

    tipo_fijado = doc["tipo_documento"] if doc["clasificacion_manual"] else None
    res = proveedor.extraer(tipo_fijado, paginas)  # posible llamada externa: fuera de transacción

    with transaccion(conn):
        _registrar_traza(conn, doc, res)
        for aviso in res.avisos:
            registrar_actividad(conn, "aviso_extraccion", aviso, exp_id, doc_id)
        if res.fallo:
            if doc["estado"] in ("recibido", "procesado"):
                conn.execute("UPDATE documentos SET estado='pendiente_analisis', actualizado=? WHERE id=?", (ahora(), doc_id))
            crear_incidencia(
                conn, "error_ia",
                f"Documento #{doc_id}: no se pudo analizar con IA ({'; '.join(res.avisos)}). "
                "Se puede reintentar con «Analizar pendientes» o «Reprocesar».",
                exp_id, doc_id,
            )
            actualizar_copia_trabajo(conn, cfg, doc_id)
            return False

        sets: dict = {}
        if not doc["clasificacion_manual"] and res.tipo_documento:
            sets |= {"tipo_documento": res.tipo_documento, "categoria": categoria_de(res.tipo_documento)}
            registrar_actividad(conn, "clasificado", f"{res.tipo_documento} ({res.motivo_tipo})", exp_id, doc_id)
        if not doc["titular_manual"] and not doc["titular_id"]:
            sets["titular_id"] = _asignar_titular(conn, doc, "\n".join(t for _, t in paginas), res.candidatos)
        fecha = next((c for c in res.candidatos if c.clave == "_fecha_documento"), None)
        if fecha and not doc["fecha_documento_manual"]:
            sets["fecha_documento"] = fecha.valor
        if doc["estado"] in ("recibido", "pendiente_analisis"):
            sets["estado"] = "procesado"
        if sets:
            asignaciones = ", ".join(f"{k}=?" for k in sets)
            conn.execute(f"UPDATE documentos SET {asignaciones}, actualizado=? WHERE id=?", (*sets.values(), ahora(), doc_id))
        doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
        candidatos = [c for c in res.candidatos if not c.clave.startswith("_")]
        n = guardar_candidatos(conn, exp_id, doc_id, doc["titular_id"], candidatos)
        registrar_actividad(conn, "extraccion", f"proveedor={proveedor.nombre}, datos={n}", exp_id, doc_id)
        actualizar_copia_trabajo(conn, cfg, doc_id)
    return True


def _registrar_traza(conn, doc, res) -> None:
    t = res.traza
    if t.get("enviado"):
        registrar_actividad(
            conn, "extraccion_ia_externa",
            f"proveedor={t.get('proveedor')}, modelo={t.get('modelo')}, páginas enviadas={t.get('paginas_enviadas')}, "
            f"caracteres enviados={t.get('caracteres_enviados')}",
            doc["expediente_id"], doc["id"],
        )
    elif t.get("cache"):
        registrar_actividad(conn, "extraccion_ia_cache", "respuesta reutilizada de la caché local; no se envió ningún dato",
                            doc["expediente_id"], doc["id"])


def analizar_pendientes(conn: sqlite3.Connection, cfg: Config, proveedor: ProveedorExtraccion | None, limite: int = 50) -> dict:
    """Reintenta el análisis de los documentos que quedaron pendientes (sin IA o tras un fallo)."""
    if proveedor is None:
        return {"analizados": 0, "pendientes": 0}
    ids = [r["id"] for r in todos(
        conn,
        "SELECT id FROM documentos WHERE estado='pendiente_analisis' AND expediente_id IS NOT NULL AND EXISTS "
        "(SELECT 1 FROM paginas_texto p WHERE p.documento_id=documentos.id AND length(trim(p.texto))>0) ORDER BY id LIMIT ?",
        (limite,),
    )]
    ok = sum(1 for i in ids if analizar(conn, cfg, i, proveedor))
    return {"analizados": ok, "pendientes": len(ids) - ok}


def _marcar_incidencia(conn, doc, tipo: str, descripcion: str) -> None:
    with transaccion(conn):
        conn.execute("UPDATE documentos SET estado='incidencia', actualizado=? WHERE id=?", (ahora(), doc["id"]))
        crear_incidencia(conn, tipo, f"Documento #{doc['id']}: {descripcion}", doc["expediente_id"], doc["id"])


def reclasificar(
    conn: sqlite3.Connection, cfg: Config, doc_id: int, tipo_documento: str | None = None,
    titular_id: int | None | str = "sin_cambio", fecha_documento: str | None = None,
    estado: str | None = None, marcado_incompleto: bool | None = None, notas: str | None = None,
) -> None:
    """Correcciones manuales de la usuaria. Ajusta la copia de trabajo (el original no se toca)."""
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if doc is None:
        raise ValueError("Documento no encontrado")
    sets: dict = {}
    if tipo_documento and tipo_documento != doc["tipo_documento"]:
        if tipo_documento not in TIPOS_DOCUMENTO:
            raise ValueError("Tipo de documento no válido")
        sets |= {"tipo_documento": tipo_documento, "categoria": categoria_de(tipo_documento), "clasificacion_manual": 1}
    if titular_id != "sin_cambio":
        if titular_id is not None:
            t = uno(conn, "SELECT expediente_id FROM titulares WHERE id=?", (titular_id,))
            if not t or t["expediente_id"] != doc["expediente_id"]:
                raise ValueError("El titular no pertenece al expediente")
        if titular_id != doc["titular_id"]:
            sets |= {"titular_id": titular_id, "titular_manual": 1}
    if fecha_documento is not None:
        fecha_documento = fecha_documento.strip() or None
        if fecha_documento and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha_documento):
            raise ValueError("Fecha no válida (AAAA-MM-DD)")
        if fecha_documento != doc["fecha_documento"]:
            sets |= {"fecha_documento": fecha_documento, "fecha_documento_manual": 1}
    if estado and estado != doc["estado"]:
        if estado not in ("procesado", "revisado", "descartado"):
            raise ValueError("Estado no válido")
        sets["estado"] = estado
    if marcado_incompleto is not None and int(marcado_incompleto) != doc["marcado_incompleto"]:
        sets["marcado_incompleto"] = int(marcado_incompleto)
    if notas is not None and notas != doc["notas"]:
        sets["notas"] = notas[:2000]
    if not sets:
        return
    with transaccion(conn):
        asignaciones = ", ".join(f"{k}=?" for k in sets)
        conn.execute(f"UPDATE documentos SET {asignaciones}, actualizado=? WHERE id=?", (*sets.values(), ahora(), doc_id))
        if "titular_id" in sets:
            # Los datos de ámbito titular extraídos de este documento siguen al documento.
            claves_titular = [k for k, v in CAMPOS.items() if v[2] == "titular"]
            marcas = ",".join("?" * len(claves_titular))
            conn.execute(
                f"UPDATE campos SET titular_id=? WHERE documento_id=? AND clave IN ({marcas})",
                (sets["titular_id"], doc_id, *claves_titular),
            )
        registrar_actividad(conn, "documento_corregido", ", ".join(sorted(sets)), doc["expediente_id"], doc_id)
        actualizar_copia_trabajo(conn, cfg, doc_id)


def texto_documento(conn: sqlite3.Connection, doc_id: int) -> list[sqlite3.Row]:
    return todos(conn, "SELECT pagina, texto, metodo FROM paginas_texto WHERE documento_id=? ORDER BY pagina", (doc_id,))
