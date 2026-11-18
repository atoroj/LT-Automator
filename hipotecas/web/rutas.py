"""Rutas de la interfaz de gestión."""
from __future__ import annotations

import mimetypes
import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from .. import almacenamiento as alm
from .. import datos as datos_srv
from .. import expedientes as exps
from ..actividad import registrar
from ..catalogo import CAMPOS
from ..checklist import requisitos as reqs
from ..checklist.evaluador import evaluar
from ..db import conectar, todos, transaccion, uno
from ..documentos import procesamiento
from ..extraccion.servicio import estado_ia
from ..gmail.conector import GmailNoConfigurado, almacen, guardar_credenciales, ruta_credenciales
from ..gmail.almacen_token import PROTECCION
from ..incidencias import TIPOS_INCIDENCIA, cerrar_incidencia, contar_abiertas
from ..informes import servicio as informes
from ..ingesta import asignar_incidencia_a_expediente, mensaje_desde_eml, procesar_mensaje
from ..seguridad import RutaNoPermitida
from ..situacion import analizar as analizar_situacion
from .app import redirigir

router = APIRouter()
Texto = Annotated[str, Form()]


def get_conn(request: Request):
    conn = conectar(request.app.state.cfg.ruta_db)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[object, Depends(get_conn)]


def render(request: Request, plantilla: str, conn, **ctx):
    app = request.app
    ctx |= {
        "request": request,
        "ok": request.query_params.get("ok", ""),
        "error": request.query_params.get("error", ""),
        "n_incidencias": contar_abiertas(conn),
        "cfg": app.state.cfg,
    }
    return app.state.plantillas.TemplateResponse(request, plantilla, ctx)


def _exp_o_404(conn, exp_id: int):
    exp = exps.obtener(conn, exp_id)
    if not exp:
        raise HTTPException(404, "Expediente no encontrado")
    return exp


def _int_opc(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v.isdigit() else None


# ---------------------------------------------------------------- Panel

@router.get("/")
def panel(request: Request, conn: Conn, code: str = "", state: str = ""):
    app = request.app
    if code and state and app.state.oauth.pendiente:
        # Vuelta de Google tras autorizar Gmail (en el PC llega aquí directamente)
        try:
            app.state.oauth.completar(app.state.cfg, str(request.url))
        except GmailNoConfigurado as e:
            return redirigir("/gmail", error=str(e))
        registrar(conn, "gmail_conectado")
        return redirigir("/gmail", ok="Gmail conectado")
    _, desc_ia = app.state.proveedor()
    return render(
        request, "panel.html", conn,
        expedientes=exps.listar(conn)[:10],
        incidencias=todos(conn, "SELECT * FROM incidencias WHERE estado='abierta' ORDER BY id DESC LIMIT 8"),
        sync=app.state.sync.estado,
        gmail_habilitado=app.state.cfg["gmail"]["habilitado"],
        gmail_conectado=almacen(app.state.cfg).existe(),
        ocr_ok=app.state.ocr.disponible(), ocr_motivo=app.state.ocr.motivo,
        desc_ia=desc_ia,
        pendientes_revision=uno(conn, "SELECT COUNT(*) n FROM documentos WHERE estado='procesado'")["n"],
        pendientes_analisis=uno(conn, "SELECT COUNT(*) n FROM documentos WHERE estado='pendiente_analisis'")["n"],
        datos_pendientes=uno(conn, "SELECT COUNT(*) n FROM campos WHERE estado='pendiente_confirmar'")["n"],
    )


# ---------------------------------------------------------------- Expedientes

@router.get("/expedientes")
def lista_expedientes(request: Request, conn: Conn, buscar: str = "", estado: str = "", colaborador: str = ""):
    return render(request, "expedientes.html", conn, expedientes=exps.listar(conn, buscar, estado, colaborador),
                  buscar=buscar, estado=estado, colaborador=colaborador, colaboradores=exps.colaboradores(conn))


@router.get("/expedientes/nuevo")
def nuevo_expediente_form(request: Request, conn: Conn):
    return render(request, "expediente_nuevo.html", conn, colaboradores=exps.colaboradores(conn))


@router.post("/expedientes/nuevo")
def nuevo_expediente(
    request: Request, conn: Conn, nombre: Texto, tipo_operacion: Texto,
    emails_contacto: Texto = "", notas: Texto = "", colaborador: Texto = "",
    titular_nombre: Texto = "", titular_perfil: Texto = "asalariado_indefinido",
    titular_dni: Texto = "", titular_email: Texto = "",
):
    try:
        exp_id = exps.crear_expediente(conn, request.app.state.cfg, nombre, tipo_operacion, emails_contacto, notas,
                                       colaborador)
        if titular_nombre.strip():
            exps.anadir_titular(conn, exp_id, titular_nombre, titular_perfil, titular_dni, titular_email)
    except exps.DatosNoValidos as e:
        return redirigir("/expedientes/nuevo", error=str(e))
    return redirigir(f"/expedientes/{exp_id}", ok="Expediente creado")


@router.get("/expedientes/{exp_id}")
def ver_expediente(request: Request, conn: Conn, exp_id: int, pestana: str = "resumen"):
    exp = _exp_o_404(conn, exp_id)
    ctx = {"exp": exp, "pestana": pestana, "titulares": exps.titulares(conn, exp_id),
           "dir_entrada": alm.dir_entrada_expediente(request.app.state.cfg, exp["referencia"], exp["nombre"]),
           "colaboradores": exps.colaboradores(conn)}
    ctx["documentos"] = todos(
        conn, "SELECT d.*, t.nombre AS titular_nombre FROM documentos d LEFT JOIN titulares t ON t.id=d.titular_id "
              "WHERE d.expediente_id=? ORDER BY d.categoria, d.id", (exp_id,))
    if pestana in ("resumen", "checklist"):
        ctx["ev"] = evaluar(conn, exp_id)
        ctx["requisitos"] = todos(conn, "SELECT * FROM requisitos ORDER BY id")
        ctx["modos"] = {r["requisito_id"]: r["modo"] for r in todos(conn, "SELECT * FROM requisitos_expediente WHERE expediente_id=?", (exp_id,))}
    if pestana == "datos":
        ctx["campos"] = datos_srv.campos_expediente(conn, exp_id)
        ctx["campos_catalogo"] = CAMPOS
    if pestana in ("calculos", "resumen"):
        ctx["a"] = analizar_situacion(conn, request.app.state.cfg, exp_id)
    if pestana == "informes":
        ctx["informes"] = informes.listar(conn, exp_id)
        ctx["ev"] = evaluar(conn, exp_id)
    if pestana == "actividad":
        ctx["actividad"] = todos(conn, "SELECT * FROM actividad WHERE expediente_id=? ORDER BY id DESC LIMIT 300", (exp_id,))
    ctx["incidencias"] = todos(conn, "SELECT * FROM incidencias WHERE expediente_id=? AND estado='abierta' ORDER BY id DESC", (exp_id,))
    return render(request, "expediente.html", conn, **ctx)


@router.post("/expedientes/{exp_id}/editar")
def editar_expediente(
    request: Request, conn: Conn, exp_id: int, nombre: Texto, tipo_operacion: Texto, estado: Texto,
    emails_contacto: Texto = "", notas: Texto = "", colaborador: Texto = "",
):
    _exp_o_404(conn, exp_id)
    try:
        exps.actualizar_expediente(conn, exp_id, nombre=nombre, tipo_operacion=tipo_operacion, estado=estado,
                                   emails_contacto=emails_contacto, notas=notas, colaborador=colaborador or None)
    except exps.DatosNoValidos as e:
        return redirigir(f"/expedientes/{exp_id}", error=str(e))
    return redirigir(f"/expedientes/{exp_id}", ok="Expediente actualizado")


@router.post("/expedientes/{exp_id}/observaciones")
def editar_observaciones(request: Request, conn: Conn, exp_id: int, observaciones_informe: Texto = ""):
    _exp_o_404(conn, exp_id)
    exps.actualizar_expediente(conn, exp_id, observaciones_informe=observaciones_informe[:10000])
    return redirigir(f"/expedientes/{exp_id}?pestana=informes", ok="Observaciones guardadas")


@router.post("/expedientes/{exp_id}/titulares")
def anadir_titular(request: Request, conn: Conn, exp_id: int, nombre: Texto, perfil_laboral: Texto,
                   dni_nie: Texto = "", email: Texto = ""):
    _exp_o_404(conn, exp_id)
    try:
        exps.anadir_titular(conn, exp_id, nombre, perfil_laboral, dni_nie, email)
    except exps.DatosNoValidos as e:
        return redirigir(f"/expedientes/{exp_id}", error=str(e))
    return redirigir(f"/expedientes/{exp_id}", ok="Titular añadido")


@router.post("/titulares/{tid}/editar")
def editar_titular(request: Request, conn: Conn, tid: int, nombre: Texto, perfil_laboral: Texto,
                   dni_nie: Texto = "", email: Texto = ""):
    t = uno(conn, "SELECT * FROM titulares WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    try:
        exps.actualizar_titular(conn, tid, nombre, perfil_laboral, dni_nie, email)
    except exps.DatosNoValidos as e:
        return redirigir(f"/expedientes/{t['expediente_id']}", error=str(e))
    return redirigir(f"/expedientes/{t['expediente_id']}", ok="Titular actualizado")


# ---------------------------------------------------------------- Documentos

def _leer_subida(request: Request, f: UploadFile) -> bytes:
    limite = request.app.state.cfg["documentos"]["tamano_maximo_mb"] * 1024 * 1024
    datos = f.file.read(limite + 1)
    if len(datos) > limite:
        raise ValueError(f"«{f.filename}» supera el tamaño máximo")
    return datos


@router.post("/expedientes/{exp_id}/documentos")
def subir_documentos(request: Request, conn: Conn, exp_id: int, archivos: Annotated[list[UploadFile], File()]):
    exp = _exp_o_404(conn, exp_id)
    cfg = request.app.state.cfg
    proveedor, _ = request.app.state.proveedor()
    n, errores = 0, []
    for f in archivos:
        if not f.filename:
            continue
        try:
            datos = _leer_subida(request, f)
        except ValueError as e:
            errores.append(str(e))
            continue
        doc_id, nuevo = procesamiento.registrar(conn, cfg, exp, f.filename, datos, procesamiento.Procedencia(origen="subida_manual"))
        if nuevo:
            procesamiento.procesar(conn, cfg, doc_id, proveedor, request.app.state.ocr)
        n += 1
    if errores:
        return redirigir(f"/expedientes/{exp_id}?pestana=documentos", error=f"{n} subido(s). " + "; ".join(errores))
    return redirigir(f"/expedientes/{exp_id}?pestana=documentos", ok=f"{n} documento(s) recibidos y procesados")


@router.get("/documentos/{doc_id}")
def ver_documento(request: Request, conn: Conn, doc_id: int):
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if not doc:
        raise HTTPException(404)
    exp = exps.obtener(conn, doc["expediente_id"]) if doc["expediente_id"] else None
    return render(
        request, "documento.html", conn, doc=doc, exp=exp,
        titulares=exps.titulares(conn, exp["id"]) if exp else [],
        paginas=procesamiento.texto_documento(conn, doc_id),
        campos=todos(conn, "SELECT * FROM campos WHERE documento_id=? ORDER BY id", (doc_id,)),
        actividad=todos(conn, "SELECT * FROM actividad WHERE documento_id=? ORDER BY id DESC", (doc_id,)),
        incidencias=todos(conn, "SELECT * FROM incidencias WHERE documento_id=? ORDER BY id DESC", (doc_id,)),
        duplicados=todos(conn, "SELECT id, origen, remitente, fecha_recepcion FROM documentos WHERE duplicado_de=?", (doc_id,)),
    )


@router.post("/documentos/{doc_id}/editar")
def editar_documento(
    request: Request, conn: Conn, doc_id: int, tipo_documento: Texto, titular_id: Texto = "",
    fecha_documento: Texto = "", estado: Texto = "", marcado_incompleto: Texto = "", notas: Texto = "",
):
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if not doc or not doc["expediente_id"]:
        raise HTTPException(404)
    cfg = request.app.state.cfg
    tipo_anterior = doc["tipo_documento"]
    try:
        procesamiento.reclasificar(
            conn, cfg, doc_id, tipo_documento=tipo_documento, titular_id=_int_opc(titular_id),
            fecha_documento=fecha_documento, estado=estado or None, marcado_incompleto=bool(marcado_incompleto), notas=notas,
        )
        if tipo_documento != tipo_anterior:
            # Nuevo análisis con IA respetando el tipo fijado por la usuaria
            proveedor, _ = request.app.state.proveedor()
            procesamiento.analizar(conn, cfg, doc_id, proveedor)
    except (ValueError, RutaNoPermitida) as e:
        return redirigir(f"/documentos/{doc_id}", error=str(e))
    return redirigir(f"/documentos/{doc_id}", ok="Documento actualizado")


@router.post("/documentos/{doc_id}/reprocesar")
def reprocesar_documento(request: Request, conn: Conn, doc_id: int):
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if not doc or not doc["expediente_id"] or doc["estado"] == "duplicado":
        raise HTTPException(404)
    conn.execute("UPDATE documentos SET estado='recibido' WHERE id=?", (doc_id,))
    proveedor, _ = request.app.state.proveedor()
    procesamiento.procesar(conn, request.app.state.cfg, doc_id, proveedor, request.app.state.ocr)
    registrar(conn, "documento_reprocesado", "", doc["expediente_id"], doc_id)
    return redirigir(f"/documentos/{doc_id}", ok="Documento reprocesado")


@router.get("/documentos/{doc_id}/archivo/{cual}")
def archivo_documento(request: Request, conn: Conn, doc_id: int, cual: str, descargar: int = 0):
    doc = uno(conn, "SELECT * FROM documentos WHERE id=?", (doc_id,))
    if not doc or cual not in ("original", "trabajo"):
        raise HTTPException(404)
    rel = doc["ruta_original"] if cual == "original" else doc["ruta_trabajo"]
    if not rel:
        raise HTTPException(404)
    try:
        ruta = alm.absoluta(request.app.state.cfg, rel)
    except RutaNoPermitida:
        raise HTTPException(400) from None
    if not ruta.is_file():
        raise HTTPException(404, "Archivo no encontrado en disco")
    tipo = {"pdf": "application/pdf", "jpeg": "image/jpeg", "png": "image/png", "tiff": "image/tiff"}.get(doc["formato"] or "")
    if not tipo or descargar:
        return FileResponse(ruta, filename=ruta.name, media_type=tipo or "application/octet-stream")
    return FileResponse(ruta, media_type=tipo, content_disposition_type="inline", filename=ruta.name,
                        headers={"Content-Security-Policy": "sandbox"})


# ---------------------------------------------------------------- Datos extraídos

@router.post("/expedientes/{exp_id}/datos")
def anadir_dato(request: Request, conn: Conn, exp_id: int, clave: Texto, valor: Texto, estado: Texto,
                titular_id: Texto = "", documento_id: Texto = "", pagina: Texto = "", nota: Texto = ""):
    _exp_o_404(conn, exp_id)
    try:
        datos_srv.anadir_manual(conn, exp_id, clave, valor, estado, _int_opc(titular_id), _int_opc(documento_id), _int_opc(pagina), nota)
    except datos_srv.CampoNoValido as e:
        return redirigir(f"/expedientes/{exp_id}?pestana=datos", error=str(e))
    return redirigir(f"/expedientes/{exp_id}?pestana=datos", ok="Dato añadido")


@router.post("/campos/{campo_id}/{accion}")
def accion_campo(request: Request, conn: Conn, campo_id: int, accion: str, valor: Texto = "", nota: Texto = "",
                 volver: Texto = ""):
    c = uno(conn, "SELECT * FROM campos WHERE id=?", (campo_id,))
    if not c:
        raise HTTPException(404)
    destino = volver if re.fullmatch(r"/[A-Za-z0-9/_\-?=&.]*", volver) and not volver.startswith("//") else f"/expedientes/{c['expediente_id']}?pestana=datos"
    try:
        if accion == "confirmar":
            datos_srv.confirmar(conn, campo_id)
        elif accion == "descartar":
            datos_srv.descartar(conn, campo_id)
        elif accion == "corregir":
            datos_srv.corregir(conn, campo_id, valor, nota)
        else:
            raise HTTPException(404)
    except datos_srv.CampoNoValido as e:
        return redirigir(destino, error=str(e))
    return redirigir(destino, ok="Dato actualizado")


@router.post("/expedientes/{exp_id}/datos/confirmar-documento")
def confirmar_todos_documento(request: Request, conn: Conn, exp_id: int, documento_id: Texto):
    """Confirma de una vez los datos pendientes de un documento, tras revisarlo."""
    did = _int_opc(documento_id)
    filas = todos(conn, "SELECT id FROM campos WHERE expediente_id=? AND documento_id=? AND estado='pendiente_confirmar'", (exp_id, did))
    with transaccion(conn):
        for f in filas:
            datos_srv.confirmar(conn, f["id"])
    return redirigir(f"/documentos/{did}", ok=f"{len(filas)} dato(s) confirmados")


# ---------------------------------------------------------------- Checklist

@router.post("/expedientes/{exp_id}/requisitos/{req_id}/modo")
def modo_requisito(request: Request, conn: Conn, exp_id: int, req_id: int, modo: Texto):
    _exp_o_404(conn, exp_id)
    try:
        reqs.fijar_modo_expediente(conn, exp_id, req_id, modo)
    except reqs.RequisitoNoValido as e:
        return redirigir(f"/expedientes/{exp_id}?pestana=checklist", error=str(e))
    registrar(conn, "requisito_modo", f"requisito #{req_id}: {modo}", exp_id)
    return redirigir(f"/expedientes/{exp_id}?pestana=checklist", ok="Requisito actualizado para este expediente")


@router.get("/requisitos")
def lista_requisitos(request: Request, conn: Conn):
    return render(request, "requisitos.html", conn, requisitos=todos(conn, "SELECT * FROM requisitos ORDER BY id"))


@router.post("/requisitos")
def guardar_requisito(
    request: Request, conn: Conn, codigo: Texto, tipo_documento: Texto, descripcion: Texto = "", ambito: Texto = "titular",
    operaciones: Texto = "*", perfiles: Texto = "*", minimo: Texto = "1", vigencia_dias: Texto = "",
    controla_caducidad: Texto = "", activo: Texto = "", requisito_id: Texto = "",
):
    datos = dict(codigo=codigo, tipo_documento=tipo_documento, descripcion=descripcion, ambito=ambito, operaciones=operaciones,
                 perfiles=perfiles, minimo=minimo, vigencia_dias=vigencia_dias, controla_caducidad=controla_caducidad, activo=activo)
    try:
        rid = reqs.guardar(conn, datos, _int_opc(requisito_id))
    except reqs.RequisitoNoValido as e:
        return redirigir("/requisitos", error=str(e))
    registrar(conn, "requisito_guardado", f"#{rid} {codigo}")
    return redirigir("/requisitos", ok="Requisito guardado")


@router.get("/requisitos/exportar")
def exportar_requisitos(request: Request, conn: Conn):
    return Response(reqs.exportar_json(conn), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="requisitos.json"'})


# ---------------------------------------------------------------- Informes

@router.post("/expedientes/{exp_id}/informes")
def generar_informe(request: Request, conn: Conn, exp_id: int):
    _exp_o_404(conn, exp_id)
    informes.generar_borrador(conn, request.app.state.cfg, exp_id)
    return redirigir(f"/expedientes/{exp_id}?pestana=informes", ok="Borrador generado. Revísalo antes de aprobarlo.")


@router.post("/informes/{inf_id}/aprobar")
def aprobar_informe(request: Request, conn: Conn, inf_id: int, referencia: Texto = "", revisado: Texto = "", nota: Texto = ""):
    inf = uno(conn, "SELECT * FROM informes WHERE id=?", (inf_id,))
    if not inf:
        raise HTTPException(404)
    try:
        informes.aprobar(conn, request.app.state.cfg, inf_id, revisado == "si", referencia, nota)
    except informes.AprobacionNoValida as e:
        return redirigir(f"/expedientes/{inf['expediente_id']}?pestana=informes", error=str(e))
    return redirigir(f"/expedientes/{inf['expediente_id']}?pestana=informes", ok="Informe aprobado. Archivos finales generados.")


@router.get("/informes/{inf_id}/{formato}")
def descargar_informe(request: Request, conn: Conn, inf_id: int, formato: str):
    inf = uno(conn, "SELECT * FROM informes WHERE id=?", (inf_id,))
    if not inf or formato not in ("docx", "pdf"):
        raise HTTPException(404)
    ruta = alm.absoluta(request.app.state.cfg, inf[f"ruta_{formato}"])
    if not ruta.is_file():
        raise HTTPException(404, "Archivo no encontrado en disco")
    tipo = "application/pdf" if formato == "pdf" else mimetypes.types_map.get(".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return FileResponse(ruta, filename=ruta.name, media_type=tipo)


# ---------------------------------------------------------------- Incidencias

@router.get("/incidencias")
def lista_incidencias(request: Request, conn: Conn, estado: str = "abierta"):
    filas = todos(
        conn, "SELECT i.*, e.referencia FROM incidencias i LEFT JOIN expedientes e ON e.id=i.expediente_id "
              "WHERE (?='' OR i.estado=?) ORDER BY i.id DESC LIMIT 500", (estado, estado))
    adjuntos = {}
    for i in filas:
        if i["gmail_mensaje_id"]:
            adjuntos[i["id"]] = todos(conn, "SELECT id, nombre_original, tamano FROM documentos WHERE gmail_mensaje_id=? AND expediente_id IS NULL",
                                      (i["gmail_mensaje_id"],))
    return render(request, "incidencias.html", conn, incidencias=filas, estado=estado, adjuntos=adjuntos,
                  tipos=TIPOS_INCIDENCIA, expedientes=exps.listar(conn, estado="abierto") + exps.listar(conn, estado="en_estudio"))


@router.post("/incidencias/{inc_id}/asignar")
def asignar_incidencia(request: Request, conn: Conn, inc_id: int, expediente_id: Texto):
    proveedor, _ = request.app.state.proveedor()
    try:
        nuevos = asignar_incidencia_a_expediente(conn, request.app.state.cfg, inc_id, int(expediente_id), proveedor, request.app.state.ocr)
    except ValueError as e:
        return redirigir("/incidencias", error=str(e))
    return redirigir(f"/expedientes/{int(expediente_id)}?pestana=documentos", ok=f"Correo asignado: {len(nuevos)} documento(s)")


@router.post("/incidencias/{inc_id}/cerrar")
def cerrar(request: Request, conn: Conn, inc_id: int, estado: Texto, resolucion: Texto = ""):
    try:
        cerrar_incidencia(conn, inc_id, estado, resolucion or "Revisada por la usuaria")
    except ValueError as e:
        return redirigir("/incidencias", error=str(e))
    return redirigir("/incidencias", ok="Incidencia actualizada")


@router.post("/importar-eml")
def importar_eml(request: Request, conn: Conn, archivo: Annotated[UploadFile, File()]):
    try:
        datos = _leer_subida(request, archivo)
        msg = mensaje_desde_eml(datos)
    except Exception as e:  # noqa: BLE001
        return redirigir("/gmail", error=f"No se pudo leer el .eml: {type(e).__name__}")
    proveedor, _ = request.app.state.proveedor()
    r = procesar_mensaje(conn, request.app.state.cfg, msg, proveedor, request.app.state.ocr)
    if r.estado == "ya_procesado":
        return redirigir("/gmail", ok="Ese mensaje ya se había procesado (no se duplica)")
    if r.expediente_id and r.estado == "procesado":
        return redirigir(f"/expedientes/{r.expediente_id}?pestana=documentos", ok=f"Correo asociado ({r.motivo}): {len(r.documentos)} adjunto(s)")
    return redirigir("/incidencias", ok=f"Correo enviado a incidencias: {r.motivo}")


# ---------------------------------------------------------------- Gmail

@router.post("/entrada/revisar")
def revisar_entrada(request: Request, conn: Conn):
    request.app.state.entrada.solicitar_ahora()
    return redirigir("/gmail", ok="Revisión de la carpeta de entrada solicitada. Recarga en unos segundos.")


@router.get("/gmail")
def estado_gmail(request: Request, conn: Conn):
    app = request.app
    cfg = app.state.cfg
    from ..carpeta_entrada import archivos_pendientes

    return render(
        request, "gmail.html", conn,
        entrada=app.state.entrada.estado, entrada_activa=app.state.entrada.activo, dir_entrada=cfg.dir_entrada,
        en_entrada=len(archivos_pendientes(cfg)),
        sync=app.state.sync.estado, sync_activo=app.state.sync.activo, oauth=app.state.oauth,
        credenciales=ruta_credenciales(cfg).exists(), ruta_credenciales=ruta_credenciales(cfg),
        conectado=almacen(cfg).existe(), proteccion=PROTECCION,
        mensajes=todos(conn, "SELECT m.*, e.referencia FROM mensajes m LEFT JOIN expedientes e ON e.id=m.expediente_id "
                             "ORDER BY COALESCE(m.procesado_en, '') DESC LIMIT 50"),
    )


@router.post("/gmail/credenciales")
def subir_credenciales(request: Request, conn: Conn, archivo: Annotated[UploadFile, File()]):
    try:
        guardar_credenciales(request.app.state.cfg, archivo.file.read(100_000))
    except GmailNoConfigurado as e:
        return redirigir("/gmail", error=str(e))
    registrar(conn, "gmail_credenciales_guardadas")
    return redirigir("/gmail", ok="Credenciales guardadas. Ahora pulsa «Conectar Gmail».")


@router.post("/gmail/conectar")
def conectar_gmail(request: Request, conn: Conn):
    try:
        request.app.state.oauth.iniciar(request.app.state.cfg)
    except GmailNoConfigurado as e:
        return redirigir("/gmail", error=str(e))
    registrar(conn, "gmail_autorizacion_iniciada")
    return redirigir("/gmail", ok="Abre el enlace de Google, elige la cuenta y acepta el acceso de solo lectura.")


@router.post("/gmail/codigo")
def completar_gmail(request: Request, conn: Conn, direccion: Texto):
    try:
        request.app.state.oauth.completar(request.app.state.cfg, direccion)
    except GmailNoConfigurado as e:
        return redirigir("/gmail", error=str(e))
    registrar(conn, "gmail_conectado")
    return redirigir("/gmail", ok="Gmail conectado")


@router.post("/gmail/sincronizar")
def sincronizar_gmail(request: Request, conn: Conn):
    if not request.app.state.cfg["gmail"]["habilitado"]:
        return redirigir("/gmail", error="Gmail está desactivado en config.toml ([gmail] habilitado = true)")
    sync = request.app.state.sync
    if sync.activo:
        sync.solicitar_ahora()
    else:
        sync.iniciar()  # el hilo sincroniza nada más arrancar
    return redirigir("/gmail", ok="Sincronización solicitada. Recarga en unos segundos.")


@router.post("/gmail/desconectar")
def desconectar_gmail(request: Request, conn: Conn):
    almacen(request.app.state.cfg).revocar_local()
    registrar(conn, "gmail_desconectado", "token local eliminado")
    return redirigir("/gmail", ok="Token local eliminado. Revoca también el acceso en tu cuenta de Google si lo deseas.")


# ---------------------------------------------------------------- Actividad

@router.get("/actividad")
def actividad(request: Request, conn: Conn):
    filas = todos(conn, "SELECT a.*, e.referencia FROM actividad a LEFT JOIN expedientes e ON e.id=a.expediente_id ORDER BY a.id DESC LIMIT 500")
    return render(request, "actividad.html", conn, actividad=filas)



# ---------------------------------------------------------------- IA

TEXTO_PRUEBA = (
    "RECIBO INDIVIDUAL DE SALARIOS\n"
    "EMPRESA: Empresa Inventada de Pruebas S.L.\n"
    "TRABAJADOR: Persona Ficticia Ejemplo\n"
    "PERIODO DE LIQUIDACIÓN: 01/10/2026 A 31/10/2026\n"
    "TOTAL DEVENGADO: 2.000,00\n"
    "LÍQUIDO TOTAL A PERCIBIR: 1.650,00 €\n"
)


@router.get("/ia")
def pagina_ia(request: Request, conn: Conn):
    cfg = request.app.state.cfg
    _, desc = request.app.state.proveedor()
    return render(
        request, "ia.html", conn, estado=estado_ia(cfg), desc=desc, texto_prueba=TEXTO_PRUEBA,
        prueba=getattr(request.app.state, "ultima_prueba_ia", None),
        pendientes=uno(conn, "SELECT COUNT(*) n FROM documentos WHERE estado='pendiente_analisis'")["n"],
        envios=todos(conn, "SELECT a.*, e.referencia FROM actividad a LEFT JOIN expedientes e ON e.id=a.expediente_id "
                           "WHERE a.tipo IN ('extraccion_ia_externa','extraccion_ia_cache') ORDER BY a.id DESC LIMIT 50"),
    )


@router.post("/ia/probar")
def probar_ia(request: Request, conn: Conn):
    """Envía un texto FICTICIO fijo para comprobar la conexión y la extracción."""
    proveedor, desc = request.app.state.proveedor()
    if proveedor is None:
        return redirigir("/ia", error=desc)
    r = proveedor.extraer(None, [(1, TEXTO_PRUEBA)])
    request.app.state.ultima_prueba_ia = {
        "fallo": r.fallo, "tipo": r.tipo_documento, "motivo": r.motivo_tipo, "avisos": r.avisos,
        "campos": [(c.clave, c.valor, c.evidencia) for c in r.candidatos],
    }
    registrar(conn, "ia_prueba", f"texto ficticio; resultado={'error' if r.fallo else 'correcto'}")
    if r.fallo:
        return redirigir("/ia", error="La prueba ha fallado: " + "; ".join(r.avisos))
    return redirigir("/ia", ok="Prueba correcta")


@router.post("/ia/analizar-pendientes")
def analizar_pendientes(request: Request, conn: Conn):
    proveedor, desc = request.app.state.proveedor()
    if proveedor is None:
        return redirigir("/ia", error=desc)
    r = procesamiento.analizar_pendientes(conn, request.app.state.cfg, proveedor)
    return redirigir("/ia", ok=f"Analizados: {r['analizados']}. Siguen pendientes: {r['pendientes']}")
