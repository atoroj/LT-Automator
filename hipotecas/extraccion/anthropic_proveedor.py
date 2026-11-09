"""Extracción con IA (Anthropic): clasifica el documento y extrae sus datos en una sola llamada.

Se activa solo con configuración explícita (config.toml):
    [ia] proveedor = "anthropic"  y  permitir_envio_externo = true
y una clave de API (variable de entorno ANTHROPIC_API_KEY o guardada cifrada desde la pantalla «IA»).

Qué sale del ordenador (y nada más):
- El texto extraído de las páginas del documento (hasta `max_caracteres_texto`), con los IBAN,
  correos electrónicos y teléfonos sustituidos por marcadores si `seudonimizar = true`.
- Si la usuaria ha fijado el tipo de documento, ese tipo.
NO se envían: archivos ni imágenes, nombre del archivo, remitente, asunto, referencia del expediente,
datos de los titulares ni de otros documentos.

Salvaguardas:
- Salida con esquema JSON estricto (tipos de documento y campos cerrados).
- Cada dato debe ir con una cita literal que se comprueba en el texto enviado; si no aparece, se descarta.
- Todo lo que propone la IA queda «pendiente de confirmar». No decide ni aprueba nada.
- Las respuestas se guardan en caché local por huella del texto: reprocesar no reenvía datos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from ..catalogo import CAMPOS, DESCRIPCION_CAMPOS, DESCRIPCION_TIPOS
from ..config import Config
from ..db import ahora, conectar
from ..documentos.texto import normalizar
from . import clave_api
from .base import Candidato, ResultadoExtraccion, normalizar_valor

log = logging.getLogger("hipotecas.ia")

VERSION_INSTRUCCIONES = "2"
CLAVE_FECHA = "_fecha_documento"


def _instrucciones() -> str:
    tipos = "\n".join(f"- {k}: {v}" for k, v in DESCRIPCION_TIPOS.items())
    campos = "\n".join(f"- {k} ({CAMPOS[k][1]}): {v}" for k, v in DESCRIPCION_CAMPOS.items())
    return f"""Eres un asistente de extracción de datos para la documentación de operaciones hipotecarias en España.
Recibes el texto de UN documento (obtenido de un PDF o por OCR, puede contener errores de lectura).

Tu tarea:
1. Clasificar el documento en uno de estos tipos (si te indican que el tipo está fijado, úsalo sin cambiarlo):
{tipos}
2. Extraer los datos que aparezcan de forma explícita en el texto, usando solo estas claves:
{campos}
- {CLAVE_FECHA} (fecha): fecha de referencia del documento (emisión, firma, fin del periodo de la nómina, fecha de cargo…).

Reglas obligatorias:
- Extrae solo lo que está escrito en el texto. No deduzcas, no calcules, no estimes y no completes datos ausentes.
- Si un dato no aparece, no lo incluyas. Es preferible omitir un dato que dar uno dudoso.
- En "valor" copia el dato tal como figura (importes en formato español, fechas como aparecen).
- En "evidencia" copia literalmente un fragmento breve del texto (una línea) que contenga el dato.
- En "pagina" indica el número de la página donde aparece, según las marcas "=== PÁGINA n ===".
- Para préstamos existentes, devuelve una entrada de cuota_prestamo, saldo_prestamo y entidad_prestamo por cada préstamo.
- Fragmentos como [IBAN], [EMAIL] o [TELEFONO] son marcadores de datos ocultados: ignóralos.
- No valores la solvencia ni opines sobre la concesión del préstamo."""


def _esquema() -> dict:
    claves = sorted(CAMPOS) + [CLAVE_FECHA]
    return {
        "type": "object",
        "properties": {
            "tipo_documento": {"type": "string", "enum": list(DESCRIPCION_TIPOS)},
            "motivo_clasificacion": {"type": "string"},
            "campos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "clave": {"type": "string", "enum": claves},
                        "valor": {"type": "string"},
                        "pagina": {"type": "integer"},
                        "evidencia": {"type": "string"},
                    },
                    "required": ["clave", "valor", "pagina", "evidencia"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["tipo_documento", "motivo_clasificacion", "campos"],
        "additionalProperties": False,
    }


_RE_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){4,7}(?:[ -]?[A-Z0-9]{1,4})?\b")
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_RE_TELEFONO = re.compile(r"(?<![\w.,])(?:\+34[\s-]?)?[6789]\d{2}[\s-]?\d{3}[\s-]?\d{3}(?![\w.,])")


def seudonimizar(texto: str) -> str:
    """Oculta datos que no se necesitan para la extracción (IBAN, correos, teléfonos)."""
    texto = _RE_IBAN.sub("[IBAN]", texto)
    texto = _RE_EMAIL.sub("[EMAIL]", texto)
    return _RE_TELEFONO.sub("[TELEFONO]", texto)


def _compacto(s: str) -> str:
    return re.sub(r"\s+", " ", normalizar(s)).strip()


class CacheIA:
    def __init__(self, cfg: Config):
        self.ruta = cfg.ruta_db

    def obtener(self, huella: str) -> dict | None:
        conn = conectar(self.ruta)
        try:
            fila = conn.execute("SELECT respuesta_json FROM cache_ia WHERE huella=?", (huella,)).fetchone()
            return json.loads(fila["respuesta_json"]) if fila else None
        finally:
            conn.close()

    def guardar(self, huella: str, modelo: str, datos: dict) -> None:
        conn = conectar(self.ruta)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO cache_ia(huella, modelo, respuesta_json, creado) VALUES (?,?,?,?)",
                (huella, modelo, json.dumps(datos, ensure_ascii=False), ahora()),
            )
        finally:
            conn.close()


class ProveedorAnthropic:
    nombre = "anthropic"
    externo = True

    def __init__(self, cfg: Config, cliente=None, cache: CacheIA | None = None):
        ia = cfg["ia"]
        self.cfg_ia = ia
        self.modelo = ia.get("modelo", "claude-opus-5-5")
        self.max_chars = int(ia.get("max_caracteres_texto", 15000))
        self.cache = cache if cache is not None else CacheIA(cfg)
        if cliente is not None:
            self.cliente = cliente
            import anthropic
            self._anthropic = anthropic
            return
        clave, origen = clave_api.obtener(cfg)
        if not clave:
            raise RuntimeError(f"Falta la clave de API ({origen})")
        try:
            import anthropic
        except ImportError as e:
            raise RuntimeError("Falta el paquete anthropic: pip install -r requirements.txt") from e
        self._anthropic = anthropic
        self.cliente = anthropic.Anthropic(
            api_key=clave,
            timeout=float(ia.get("timeout_segundos", 180)),
            max_retries=int(ia.get("max_reintentos", 2)),
        )

    def _preparar(self, paginas: list[tuple[int, str]], res: ResultadoExtraccion) -> list[tuple[int, str]]:
        enviadas, total = [], 0
        for pag, texto in paginas:
            if self.cfg_ia.get("seudonimizar", True):
                texto = seudonimizar(texto)
            if total + len(texto) > self.max_chars:
                res.avisos.append(
                    f"IA: por el límite de {self.max_chars} caracteres solo se analizaron {len(enviadas)} de "
                    f"{len(paginas)} páginas; revisa las restantes manualmente"
                )
                break
            enviadas.append((pag, texto))
            total += len(texto)
        return enviadas

    def _llamar(self, peticion: str) -> dict:
        argumentos = dict(
            model=self.modelo,
            max_tokens=16000,
            system=_instrucciones(),
            cache_control={"type": "ephemeral"},
            output_config={"effort": self.cfg_ia.get("esfuerzo", "medium"),
                           "format": {"type": "json_schema", "schema": _esquema()}},
            messages=[{"role": "user", "content": peticion}],
        )
        if self.cfg_ia.get("usar_fallback_servidor", True):
            # Si el modelo rechaza la petición, la API la reintenta en otro modelo de Anthropic.
            respuesta = self.cliente.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **argumentos
            )
        else:
            respuesta = self.cliente.messages.create(**argumentos)
        if respuesta.stop_reason == "refusal":
            raise _ErrorIA("el proveedor rechazó la petición")
        if respuesta.stop_reason == "max_tokens":
            raise _ErrorIA("respuesta incompleta (límite de tokens)")
        texto = next((b.text for b in respuesta.content if b.type == "text"), "")
        try:
            return json.loads(texto)
        except json.JSONDecodeError as e:
            raise _ErrorIA("respuesta no interpretable") from e

    def extraer(self, tipo_documento: str | None, paginas: list[tuple[int, str]]) -> ResultadoExtraccion:
        res = ResultadoExtraccion()
        enviadas = self._preparar(paginas, res)
        caracteres = sum(len(t) for _, t in enviadas)
        res.traza = {"proveedor": "anthropic", "externo": True, "modelo": self.modelo, "enviado": False,
                     "cache": False, "paginas_enviadas": [p for p, _ in enviadas], "caracteres_enviados": caracteres}
        if not enviadas or not any(t.strip() for _, t in enviadas):
            res.fallo = True
            res.avisos.append("IA: no hay texto que analizar")
            return res

        cuerpo = "\n\n".join(f"=== PÁGINA {p} ===\n{t}" for p, t in enviadas)
        cabecera = (f"El tipo de documento está fijado por la usuaria: {tipo_documento}. No lo cambies.\n\n"
                    if tipo_documento else "")
        peticion = f"{cabecera}Texto del documento:\n{cuerpo}"
        huella = hashlib.sha256(f"{self.modelo}|{VERSION_INSTRUCCIONES}|{peticion}".encode()).hexdigest()

        a = self._anthropic
        datos = self.cache.obtener(huella)
        if datos is not None:
            res.traza |= {"cache": True, "caracteres_enviados": 0}
        else:
            res.traza["enviado"] = True
            try:
                datos = self._llamar(peticion)
            except _ErrorIA as e:
                return self._fallo(res, str(e))
            except a.AuthenticationError:
                res.traza["enviado"] = False
                return self._fallo(res, "clave de API no válida")
            except a.RateLimitError:
                return self._fallo(res, "límite de uso alcanzado; reintentar más tarde")
            except a.APIStatusError as e:
                return self._fallo(res, f"error {e.status_code} del proveedor")
            except a.APIConnectionError:
                res.traza["enviado"] = False
                return self._fallo(res, "sin conexión con el proveedor")
            self.cache.guardar(huella, self.modelo, datos)

        return self._interpretar(datos, tipo_documento, enviadas, res)

    def _fallo(self, res: ResultadoExtraccion, motivo: str) -> ResultadoExtraccion:
        res.fallo = True
        res.avisos.append(f"IA: {motivo}")
        return res

    def _interpretar(self, datos: dict, tipo_fijado, enviadas, res: ResultadoExtraccion) -> ResultadoExtraccion:
        if not tipo_fijado:
            tipo = datos.get("tipo_documento")
            res.tipo_documento = tipo if tipo in DESCRIPCION_TIPOS else "desconocido"
            res.motivo_tipo = f"IA: {str(datos.get('motivo_clasificacion', ''))[:160]}"
        por_pagina = {p: _compacto(t) for p, t in enviadas}
        descartados = 0
        for item in datos.get("campos", []):
            clave, pag = item.get("clave"), item.get("pagina")
            evid = str(item.get("evidencia", ""))[:300]
            if (clave not in CAMPOS and clave != CLAVE_FECHA) or pag not in por_pagina:
                descartados += 1
                continue
            if not evid.strip() or _compacto(evid) not in por_pagina[pag]:
                descartados += 1  # la cita no está en el texto: posible dato inventado
                continue
            tipo = "fecha" if clave == CLAVE_FECHA else CAMPOS[clave][1]
            valor = normalizar_valor(tipo, str(item.get("valor", "")))
            if valor is None or ("[" in valor and "]" in valor):
                descartados += 1
                continue
            if tipo in ("importe", "entero") and not _valor_en_evidencia(valor, tipo, evid):
                descartados += 1  # el número no figura en la cita
                continue
            res.candidatos.append(Candidato(clave, valor, pag, evid[:160], origen="extraccion_ia"))
        if descartados:
            res.avisos.append(f"IA: {descartados} dato(s) descartados por no poder verificarse en el texto")
        res.traza["campos_recibidos"] = len(datos.get("campos", []))
        return res


def _valor_en_evidencia(valor: str, tipo: str, evidencia: str) -> bool:
    """Comprueba que el número propuesto aparece en la cita (evita cifras recalculadas)."""
    numeros = set()
    for m in re.finditer(r"\d[\d.,]*", evidencia):
        v = normalizar_valor(tipo, m.group(0).rstrip(".,"))
        if v is not None:
            numeros.add(v)
    return valor in numeros


class _ErrorIA(Exception):
    pass
