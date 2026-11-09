"""Carga de configuración (TOML) con valores por defecto seguros."""
from __future__ import annotations

import copy
import ipaddress
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

DEFECTO: dict = {
    # hosts_permitidos: nombres con los que se accede desde otros dispositivos (p. ej. el de Tailscale)
    "servidor": {"host": "127.0.0.1", "puerto": 8765, "abrir_navegador": True, "hosts_permitidos": []},
    "copias": {"dias_conservar": 30},
    "rutas": {"datos": "datos"},
    "carpetas": {
        "raiz": "GESTOR HIPOTECARIO",
        "entrada": "00 - ENTRADA",
        "expedientes": "02 - EXPEDIENTES",
        "incidencias": "03 - INCIDENCIAS",
        "vigilar_entrada": True,
        "intervalo_segundos": 15,
        "segundos_estabilidad": 5,
    },
    "expedientes": {
        "prefijo_referencia": "EXP",
        "carpeta_entrada": "00_Entrada_Originales",
        "subcarpetas": {
            "identificacion": "01_Identificacion",
            "laboral": "02_Laboral",
            "economica_fiscal": "03_Economica_Fiscal",
            "endeudamiento": "04_Endeudamiento",
            "vivienda": "05_Vivienda",
            "informes": "06_Informes",
            "otros": "07_Otros",
        },
    },
    "documentos": {
        "tamano_maximo_mb": 25,
        "extensiones_admitidas": ["pdf", "jpg", "jpeg", "png", "tif", "tiff"],
        "min_caracteres_pagina": 40,
    },
    "ocr": {"habilitado": True, "tesseract_cmd": "", "idioma": "spa", "dpi": 300},
    "gmail": {
        "habilitado": False,
        "etiqueta": "Hipotecas/Procesar",
        "intervalo_minutos": 10,
        "asociar_por_remitente": True,
        "incluir_remitentes_de_expedientes": True,
        "dias_remitentes": 30,
        "consulta": "",
        "patron_referencia": r"EXP-\d{4}-\d{4}",
        "ignorar_imagenes_inline_menores_kb": 30,
        "max_mensajes_por_sincronizacion": 50,
        "max_intentos_por_mensaje": 5,
    },
    "ia": {
        "proveedor": "anthropic",
        "permitir_envio_externo": False,
        "modelo": "claude-opus-5-5",
        "esfuerzo": "medium",
        "max_caracteres_texto": 15000,
        "seudonimizar": True,
        "usar_fallback_servidor": True,
        "timeout_segundos": 180,
        "max_reintentos": 2,
    },
    "calculos": {
        "tipo_interes_anual": 3.0,
        "plazo_anios": 30,
        "ratio_endeudamiento_maximo": 35.0,
        "ltv_maximo": 80.0,
        "financiacion_maxima_precio": 80.0,
        "porcentaje_impuestos_compra": 8.0,
        "gastos_fijos_estimados": 2500.0,
        "incremento_estres_puntos": 1.0,
        "nominas_para_media": 3,
    },
}

CATEGORIAS = ["identificacion", "laboral", "economica_fiscal", "endeudamiento", "vivienda", "informes", "otros"]


def _fusionar(base: dict, extra: dict) -> dict:
    res = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(res.get(k), dict):
            res[k] = _fusionar(res[k], v)
        else:
            res[k] = v
    return res


@dataclass
class Config:
    datos: dict = field(default_factory=lambda: copy.deepcopy(DEFECTO))
    origen: str = "valores por defecto"

    def __getitem__(self, seccion: str) -> dict:
        return self.datos[seccion]

    @property
    def dir_datos(self) -> Path:
        p = Path(self.datos["rutas"]["datos"])
        if not p.is_absolute():
            p = RAIZ_PROYECTO / p
        return p.resolve()

    @property
    def dir_gestor(self) -> Path:
        """Carpeta visible «GESTOR HIPOTECARIO» (siempre dentro de [rutas] datos)."""
        return self.dir_datos / self.datos["carpetas"]["raiz"]

    @property
    def dir_entrada(self) -> Path:
        return self.dir_gestor / self.datos["carpetas"]["entrada"]

    @property
    def dir_expedientes(self) -> Path:
        return self.dir_gestor / self.datos["carpetas"]["expedientes"]

    @property
    def dir_incidencias(self) -> Path:
        return self.dir_gestor / self.datos["carpetas"]["incidencias"]

    @property
    def dir_expedientes_antiguo(self) -> Path:
        """Ubicación de los expedientes creados con versiones anteriores (se siguen leyendo ahí)."""
        return self.dir_datos / "expedientes"

    @property
    def dir_secretos(self) -> Path:
        return self.dir_datos / "secretos"

    @property
    def dir_logs(self) -> Path:
        return self.dir_datos / "logs"

    @property
    def ruta_db(self) -> Path:
        return self.dir_datos / "hipotecas.db"

    def asegurar_directorios(self) -> None:
        for d in (self.dir_datos, self.dir_entrada, self.dir_expedientes, self.dir_incidencias,
                  self.dir_secretos, self.dir_logs):
            d.mkdir(parents=True, exist_ok=True)


def validar(cfg: Config) -> None:
    host = cfg["servidor"]["host"]
    if host != "localhost":
        try:
            ip = ipaddress.ip_address(host)
        except ValueError as e:
            raise ValueError(f"servidor.host no válido: {host!r}") from e
        if not ip.is_loopback:
            raise ValueError(
                "Por seguridad la aplicación solo escucha en 127.0.0.1 (loopback). "
                f"Se ha configurado {host!r}, que la expondría a la red."
            )
    subs = cfg["expedientes"]["subcarpetas"]
    faltan = [c for c in CATEGORIAS if c not in subs]
    if faltan:
        raise ValueError(f"Faltan subcarpetas en [expedientes.subcarpetas]: {faltan}")
    c = cfg["carpetas"]
    for nombre in list(subs.values()) + [cfg["expedientes"]["carpeta_entrada"], c["raiz"], c["entrada"],
                                         c["expedientes"], c["incidencias"]]:
        if not nombre or any(c in nombre for c in '\\/:*?"<>|') or nombre in (".", ".."):
            raise ValueError(f"Nombre de subcarpeta no válido: {nombre!r}")


def cargar(ruta: str | os.PathLike | None = None, sobrescribir: dict | None = None) -> Config:
    ruta = ruta or os.environ.get("HIPOTECAS_CONFIG") or (RAIZ_PROYECTO / "config.toml")
    ruta = Path(ruta)
    datos = copy.deepcopy(DEFECTO)
    origen = "valores por defecto (no existe config.toml)"
    if ruta.exists():
        with open(ruta, "rb") as f:
            datos = _fusionar(datos, tomllib.load(f))
        origen = str(ruta)
    if sobrescribir:
        datos = _fusionar(datos, sobrescribir)
    cfg = Config(datos=datos, origen=origen)
    validar(cfg)
    return cfg
