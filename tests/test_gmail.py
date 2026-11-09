"""Conector de Gmail con un servicio simulado (sin red ni credenciales)."""
import base64
import re

from hipotecas import expedientes as exps
from hipotecas.db import conectar, todos
from hipotecas.gmail.conector import ClienteGmail
from hipotecas.gmail.sincronizador import Sincronizador


class _Ejec:
    def __init__(self, valor):
        self.valor = valor

    def execute(self):
        return self.valor


class ServicioFalso:
    """Imita users().labels/messages/attachments de la API de Gmail."""

    def __init__(self, mensajes: dict, etiquetas: dict):
        self.mensajes, self.etiquetas = mensajes, etiquetas
        self.listados_con_label = []
        self.consultas = []

    def users(self):
        return self

    def labels(self):
        s = self

        class L:
            def list(self, userId):
                return _Ejec({"labels": [{"id": k, "name": v} for k, v in s.etiquetas.items()]})
        return L()

    def messages(self):
        s = self

        class M:
            def list(self, userId, labelIds=None, q=None, pageToken=None, maxResults=100, includeSpamTrash=False):
                if labelIds:
                    s.listados_con_label.append(tuple(labelIds))
                    ids = [mid for mid, m in s.mensajes.items() if labelIds[0] in m["labelIds"]]
                else:
                    s.consultas.append(q)
                    remitentes = re.search(r"from:\((.*)\)", q).group(1).split(" OR ")
                    ids = [mid for mid, m in s.mensajes.items()
                           if any(r in m["payload"]["headers"][0]["value"] for r in remitentes) and m["_adj"]]
                return _Ejec({"messages": [{"id": i} for i in ids]})

            def get(self, userId, id, format):
                return _Ejec(s.mensajes[id])

            def attachments(self):
                class A:
                    def get(self, userId, messageId, id):
                        return _Ejec({"data": s.mensajes[messageId]["_adj"][id]})
                return A()
        return M()


def _mensaje(mid, remitente, asunto, adjuntos, etiqueta="L1"):
    partes, datos = [], {}
    for i, (nombre, contenido) in enumerate(adjuntos):
        aid = f"att-{mid}-{i}"
        datos[aid] = base64.urlsafe_b64encode(contenido).decode()
        partes.append({"partId": str(i + 1), "filename": nombre, "mimeType": "application/pdf",
                       "headers": [{"name": "Content-Disposition", "value": "attachment"}], "body": {"attachmentId": aid}})
    return {"id": mid, "labelIds": [etiqueta], "internalDate": "1762000000000", "_adj": datos,
            "payload": {"headers": [{"name": "From", "value": remitente}, {"name": "Subject", "value": asunto}],
                        "parts": [{"partId": "0", "mimeType": "text/plain", "filename": "", "body": {"data": ""}}, *partes]}}


def test_sincronizacion_solo_etiqueta_y_sin_reprocesar(cfg, conn, ocr, docs_ficticios, monkeypatch):
    exp_id = exps.crear_expediente(conn, cfg, "Gmail", "compra_vivienda_habitual")
    exps.anadir_titular(conn, exp_id, "Lucía Prueba Ficticia", "asalariado_indefinido")
    ref = exps.obtener(conn, exp_id)["referencia"]
    pdf = docs_ficticios["contrato_arras.pdf"].read_bytes()
    servicio = ServicioFalso(
        {"a": _mensaje("a", "x@y.invalid", f"Arras {ref}", [("arras.pdf", pdf)]),
         "b": _mensaje("b", "x@y.invalid", "Otro correo sin etiqueta", [("x.pdf", pdf)], etiqueta="INBOX")},
        {"L1": "Hipotecas/Procesar", "INBOX": "INBOX"},
    )
    cfg.datos["gmail"]["habilitado"] = True
    monkeypatch.setattr("hipotecas.gmail.sincronizador.almacen", lambda c: type("A", (), {"existe": lambda self: True})())
    sync = Sincronizador(cfg, ocr, fabrica_cliente=lambda: ClienteGmail(cfg, servicio))

    c1 = sync.sincronizar()
    assert c1["procesados"] == 1 and c1["encontrados"] == 1
    assert servicio.listados_con_label == [("L1",)]  # solo se consulta la etiqueta, nunca el buzón completo
    docs = todos(conn, "SELECT * FROM documentos")
    assert len(docs) == 1 and docs[0]["gmail_mensaje_id"] == "a" and docs[0]["remitente"] == "x@y.invalid"

    # Reinicio: un nuevo sincronizador no reprocesa lo ya hecho
    c2 = Sincronizador(cfg, ocr, fabrica_cliente=lambda: ClienteGmail(cfg, servicio)).sincronizar()
    assert c2["nuevos"] == 0
    assert len(todos(conn, "SELECT * FROM documentos")) == 1


def test_mensaje_con_error_se_reintenta(cfg, conn, ocr, monkeypatch):
    servicio = ServicioFalso({"z": _mensaje("z", "x@y.invalid", "s", [])}, {"L1": "Hipotecas/Procesar"})
    cfg.datos["gmail"]["habilitado"] = True
    monkeypatch.setattr("hipotecas.gmail.sincronizador.almacen", lambda c: type("A", (), {"existe": lambda self: True})())

    class ClienteRoto(ClienteGmail):
        def obtener_mensaje(self, mid):
            raise ConnectionError("red caída")

    s = Sincronizador(cfg, ocr, fabrica_cliente=lambda: ClienteRoto(cfg, servicio))
    assert s.sincronizar()["errores"] == 1
    fila = conectar(cfg.ruta_db).execute("SELECT * FROM mensajes WHERE mensaje_id='z'").fetchone()
    assert fila["estado"] == "error" and fila["intentos"] == 1
    # en la siguiente sincronización vuelve a intentarse y, ya sin error, queda procesado (a incidencias, sin expediente)
    s2 = Sincronizador(cfg, ocr, fabrica_cliente=lambda: ClienteGmail(cfg, servicio))
    assert s2.sincronizar()["incidencias"] == 1


def test_recoge_correos_de_remitentes_de_expedientes(cfg, conn, ocr, docs_ficticios, monkeypatch):
    """Sin etiqueta: el correo con adjuntos de un contacto del expediente se recoge y se asocia solo."""
    exp_id = exps.crear_expediente(conn, cfg, "Remitente", "compra_vivienda_habitual", "cliente@ficticio.invalid")
    pdf = docs_ficticios["nota_simple.pdf"].read_bytes()
    servicio = ServicioFalso(
        {"r1": _mensaje("r1", "Cliente <cliente@ficticio.invalid>", "te mando la nota", [("nota.pdf", pdf)], etiqueta="INBOX"),
         "r2": _mensaje("r2", "otro@ajeno.invalid", "publicidad", [("x.pdf", pdf)], etiqueta="INBOX")},
        {"L1": "Hipotecas/Procesar", "INBOX": "INBOX"},
    )
    cfg.datos["gmail"]["habilitado"] = True
    monkeypatch.setattr("hipotecas.gmail.sincronizador.almacen", lambda c: type("A", (), {"existe": lambda self: True})())
    c = Sincronizador(cfg, ocr, fabrica_cliente=lambda: ClienteGmail(cfg, servicio)).sincronizar()
    assert c["procesados"] == 1 and c["encontrados"] == 1
    assert servicio.consultas and all("has:attachment" in q and "from:(" in q for q in servicio.consultas)
    docs = todos(conn, "SELECT * FROM documentos")
    assert len(docs) == 1 and docs[0]["expediente_id"] == exp_id and docs[0]["gmail_mensaje_id"] == "r1"
