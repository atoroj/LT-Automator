# Imagen del gestor hipotecario para el servidor (Ubuntu + Docker).
# Incluye Tesseract con español para el OCR de fotos y PDF escaneados.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Madrid \
    HIPOTECAS_CONTENEDOR=1 \
    HIPOTECAS_CONFIG=/config/config.toml

RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-spa tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home gestor

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY hipotecas ./hipotecas

USER gestor
EXPOSE 8765
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/static/estilo.css', timeout=5)"
CMD ["python", "-m", "hipotecas", "--sin-navegador"]
