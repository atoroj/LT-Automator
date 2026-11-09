"""Detección de formato, extracción de texto de PDF digitales y OCR local (Tesseract)."""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("hipotecas.texto")

FORMATOS_IMAGEN = {"jpeg", "png", "tiff"}


def detectar_formato(cabecera: bytes) -> str | None:
    """Detecta el formato real por la firma del archivo (no por la extensión)."""
    if cabecera.startswith(b"%PDF"):
        return "pdf"
    if cabecera.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if cabecera.startswith(b"II*\x00") or cabecera.startswith(b"MM\x00*"):
        return "tiff"
    return None


EXTENSION_FORMATO = {"pdf": "pdf", "jpg": "jpeg", "jpeg": "jpeg", "png": "png", "tif": "tiff", "tiff": "tiff"}


class ErrorLectura(Exception):
    """Documento ilegible, protegido o no procesable. El mensaje es apto para mostrar."""


@dataclass
class ResultadoTexto:
    paginas: list[tuple[int, str, str]] = field(default_factory=list)  # (pagina, texto, metodo)
    num_paginas: int = 0
    avisos: list[str] = field(default_factory=list)

    @property
    def metodo(self) -> str:
        metodos = {m for _, t, m in self.paginas if t.strip()}
        if not metodos:
            return "ninguno"
        return "+".join(sorted(metodos))

    @property
    def total_caracteres(self) -> int:
        return sum(len(t.strip()) for _, t, _ in self.paginas)


class MotorOCR:
    def __init__(self, cfg_ocr: dict):
        self.cfg = cfg_ocr
        self._disponible: bool | None = None
        self.motivo = ""

    def disponible(self) -> bool:
        if self._disponible is not None:
            return self._disponible
        if not self.cfg.get("habilitado", True):
            self._disponible, self.motivo = False, "OCR desactivado en configuración"
            return False
        try:
            import pytesseract

            if self.cfg.get("tesseract_cmd"):
                if not Path(self.cfg["tesseract_cmd"]).exists():
                    raise FileNotFoundError(self.cfg["tesseract_cmd"])
                pytesseract.pytesseract.tesseract_cmd = self.cfg["tesseract_cmd"]
            version = pytesseract.get_tesseract_version()
            idiomas = pytesseract.get_languages(config="")
            idioma = self.cfg.get("idioma", "spa")
            if idioma not in idiomas:
                self._disponible = False
                self.motivo = f"Tesseract {version} instalado pero sin el idioma '{idioma}'"
                return False
            self._disponible, self.motivo = True, f"Tesseract {version} ({idioma})"
        except Exception as e:  # noqa: BLE001 - cualquier fallo implica OCR no disponible
            self._disponible = False
            self.motivo = f"Tesseract no disponible ({type(e).__name__})"
        return self._disponible

    def imagen_a_texto(self, imagen) -> str:
        import pytesseract

        return pytesseract.image_to_string(imagen, lang=self.cfg.get("idioma", "spa"))


def _texto_pdf(datos: bytes, min_chars: int, ocr: MotorOCR) -> ResultadoTexto:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    res = ResultadoTexto()
    try:
        lector = PdfReader(io.BytesIO(datos))
        if lector.is_encrypted:
            try:
                if not lector.decrypt(""):
                    raise ErrorLectura("PDF protegido con contraseña")
            except ErrorLectura:
                raise
            except Exception as e:  # noqa: BLE001
                raise ErrorLectura("PDF protegido con contraseña") from e
        paginas = lector.pages
        res.num_paginas = len(paginas)
    except ErrorLectura:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        raise ErrorLectura(f"PDF dañado o ilegible ({type(e).__name__})") from e

    escaneadas: list[int] = []
    for i, pagina in enumerate(paginas, start=1):
        try:
            texto = pagina.extract_text() or ""
        except Exception:  # noqa: BLE001
            texto = ""
        if len(texto.strip()) >= min_chars:
            res.paginas.append((i, texto, "pdf_digital"))
        else:
            escaneadas.append(i)
            res.paginas.append((i, texto, "pdf_digital"))

    if escaneadas:
        if ocr.disponible():
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(datos)
            escala = ocr.cfg.get("dpi", 300) / 72
            por_pagina = {p: (t, m) for p, t, m in res.paginas}
            for num in escaneadas:
                imagen = pdf[num - 1].render(scale=escala).to_pil()
                por_pagina[num] = (ocr.imagen_a_texto(imagen), "ocr")
            res.paginas = [(p, t, m) for p, (t, m) in sorted(por_pagina.items())]
        else:
            res.avisos.append(
                f"{len(escaneadas)} página(s) sin texto digital; requieren OCR ({ocr.motivo})"
            )
    return res


def _texto_imagen(datos: bytes, ocr: MotorOCR) -> ResultadoTexto:
    from PIL import Image, ImageSequence, UnidentifiedImageError

    res = ResultadoTexto()
    try:
        img = Image.open(io.BytesIO(datos))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ErrorLectura(f"Imagen dañada o ilegible ({type(e).__name__})") from e
    marcos = [m.copy() for m in ImageSequence.Iterator(img)]
    res.num_paginas = len(marcos)
    if not ocr.disponible():
        res.avisos.append(f"Imagen: requiere OCR ({ocr.motivo})")
        res.paginas = [(i, "", "ninguno") for i in range(1, len(marcos) + 1)]
        return res
    for i, marco in enumerate(marcos, start=1):
        res.paginas.append((i, ocr.imagen_a_texto(marco.convert("RGB")), "ocr"))
    return res


def extraer_texto(datos: bytes, formato: str, min_chars: int, ocr: MotorOCR) -> ResultadoTexto:
    if formato == "pdf":
        return _texto_pdf(datos, min_chars, ocr)
    if formato in FORMATOS_IMAGEN:
        return _texto_imagen(datos, ocr)
    raise ErrorLectura(f"Formato no admitido: {formato}")


def normalizar(texto: str) -> str:
    """Mayúsculas sin tildes, para comparar textos. Conserva la longitud aproximada."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[ \t]+", " ", t.upper())
