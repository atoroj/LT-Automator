"""Sincronización periódica de Gmail en segundo plano.

- Al arrancar la aplicación se lanza una sincronización: los mensajes etiquetados que no
  constan como procesados se recuperan automáticamente (recuperación tras reinicio).
- Después se repite cada `intervalo_minutos`.
- Un cerrojo evita dos sincronizaciones simultáneas (periódica y manual).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from ..actividad import registrar
from ..config import Config
from ..db import ahora, conectar, todos
from ..documentos.procesamiento import analizar_pendientes
from ..documentos.texto import MotorOCR
from ..extraccion.servicio import obtener_proveedor
from ..ingesta import debe_procesarse, procesar_mensaje, registrar_error_mensaje
from .conector import ClienteGmail, GmailNoConfigurado, almacen

log = logging.getLogger("hipotecas.gmail")


def consultas_adicionales(conn, cfg: Config) -> list[str]:
    """Búsquedas de Gmail además de la etiqueta: remitentes de expedientes abiertos y consulta configurable.

    Nunca se recorre la bandeja completa: toda búsqueda exige adjuntos y un remitente concreto,
    o es la consulta que tú hayas escrito en config.toml.
    """
    g = cfg["gmail"]
    consultas = []
    if g.get("incluir_remitentes_de_expedientes", True):
        from ..expedientes import emails_de_expediente

        emails = sorted({e for exp in todos(conn, "SELECT * FROM expedientes WHERE estado != 'cerrado'")
                         for e in emails_de_expediente(conn, exp)})
        dias = int(g.get("dias_remitentes", 30))
        for i in range(0, len(emails), 20):  # bloques para no superar la longitud de búsqueda
            consultas.append(f"has:attachment newer_than:{dias}d from:({' OR '.join(emails[i:i + 20])})")
    if (g.get("consulta") or "").strip():
        consultas.append(g["consulta"].strip())
    return consultas


@dataclass
class EstadoSync:
    ultima_ejecucion: str = ""
    ultimo_resultado: str = "Sin ejecutar"
    ultimo_error: str = ""
    en_curso: bool = False
    contadores: dict = field(default_factory=dict)


class Sincronizador:
    def __init__(self, cfg: Config, ocr: MotorOCR, fabrica_cliente=None):
        self.cfg = cfg
        self.ocr = ocr
        self.estado = EstadoSync()
        self._cerrojo = threading.Lock()
        self._parar = threading.Event()
        self._despertar = threading.Event()
        self._hilo: threading.Thread | None = None
        self._fabrica = fabrica_cliente or (lambda: ClienteGmail(cfg))

    @property
    def activo(self) -> bool:
        return bool(self._hilo and self._hilo.is_alive())

    def iniciar(self) -> None:
        if self.activo or not self.cfg["gmail"]["habilitado"]:
            return
        self._parar.clear()
        self._hilo = threading.Thread(target=self._bucle, name="sync-gmail", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._parar.set()
        self._despertar.set()

    def solicitar_ahora(self) -> None:
        if not self.activo:
            threading.Thread(target=self.sincronizar, name="sync-gmail-manual", daemon=True).start()
        else:
            self._despertar.set()

    def _bucle(self) -> None:
        intervalo = max(1, int(self.cfg["gmail"]["intervalo_minutos"])) * 60
        while not self._parar.is_set():
            self.sincronizar()
            self._despertar.wait(intervalo)
            self._despertar.clear()

    def sincronizar(self) -> dict:
        if not self._cerrojo.acquire(blocking=False):
            return {"omitida": "ya hay una sincronización en curso"}
        conn = None
        cont = {"encontrados": 0, "nuevos": 0, "procesados": 0, "incidencias": 0, "errores": 0}
        try:
            self.estado.en_curso = True
            self.estado.ultima_ejecucion = ahora()
            if not almacen(self.cfg).existe():
                raise GmailNoConfigurado("Gmail no está conectado")
            cliente = self._fabrica()
            conn = conectar(self.cfg.ruta_db)
            etiqueta = self.cfg["gmail"]["etiqueta"]
            label_id = cliente.id_etiqueta(etiqueta) if etiqueta else None
            consultas = consultas_adicionales(conn, self.cfg)
            if not label_id and not consultas:
                raise GmailNoConfigurado(f"No existe la etiqueta «{etiqueta}» en Gmail")
            proveedor, _ = obtener_proveedor(self.cfg)
            ids = cliente.ids_con_etiqueta(label_id) if label_id else []
            for q in consultas:
                ids += cliente.ids_por_consulta(q)
            ids = list(dict.fromkeys(ids))  # sin repetidos, conservando el orden
            cont["encontrados"] = len(ids)
            pendientes = [i for i in reversed(ids) if debe_procesarse(conn, self.cfg, i)]  # más antiguos primero
            cont["nuevos"] = len(pendientes)
            for mid in pendientes[: int(self.cfg["gmail"]["max_mensajes_por_sincronizacion"])]:
                if self._parar.is_set():
                    break
                try:
                    msg = cliente.obtener_mensaje(mid)
                    r = procesar_mensaje(conn, self.cfg, msg, proveedor, self.ocr)
                    cont["procesados" if r.estado == "procesado" else "incidencias"] += 1
                except Exception as e:  # noqa: BLE001 - se reintenta en la próxima sincronización
                    log.exception("Error procesando mensaje %s", mid)
                    cont["errores"] += 1
                    registrar_error_mensaje(conn, self.cfg, mid, "gmail", f"{type(e).__name__}: {str(e)[:200]}")
            # Reintenta los documentos que quedaron pendientes de análisis (IA caída o recién configurada)
            cont["reanalizados"] = analizar_pendientes(conn, self.cfg, proveedor, limite=20)["analizados"]
            registrar(conn, "sincronizacion_gmail", ", ".join(f"{k}={v}" for k, v in cont.items()))
            self.estado.ultimo_resultado = "Correcta"
            self.estado.ultimo_error = ""
        except GmailNoConfigurado as e:
            self.estado.ultimo_resultado, self.estado.ultimo_error = "No configurado", str(e)
        except Exception as e:  # noqa: BLE001
            log.exception("Fallo de sincronización")
            self.estado.ultimo_resultado, self.estado.ultimo_error = "Error", f"{type(e).__name__}: {str(e)[:200]}"
        finally:
            self.estado.contadores = cont
            self.estado.en_curso = False
            if conn is not None:
                conn.close()
            self._cerrojo.release()
        return cont
