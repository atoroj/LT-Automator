"""Base de datos SQLite local (esquema y utilidades)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

VERSION_ESQUEMA = 1

ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (clave TEXT PRIMARY KEY, valor TEXT);

CREATE TABLE IF NOT EXISTS expedientes (
    id INTEGER PRIMARY KEY,
    referencia TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    tipo_operacion TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'abierto',
    emails_contacto TEXT NOT NULL DEFAULT '',
    colaborador TEXT NOT NULL DEFAULT 'Propio',
    notas TEXT NOT NULL DEFAULT '',
    observaciones_informe TEXT NOT NULL DEFAULT '',
    carpeta TEXT NOT NULL,
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS titulares (
    id INTEGER PRIMARY KEY,
    expediente_id INTEGER NOT NULL REFERENCES expedientes(id),
    orden INTEGER NOT NULL DEFAULT 1,
    nombre TEXT NOT NULL,
    dni_nie TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    perfil_laboral TEXT NOT NULL,
    creado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY,
    expediente_id INTEGER REFERENCES expedientes(id),
    titular_id INTEGER REFERENCES titulares(id),
    nombre_original TEXT NOT NULL,
    ruta_original TEXT NOT NULL,
    ruta_trabajo TEXT,
    sha256 TEXT NOT NULL,
    tamano INTEGER NOT NULL,
    extension TEXT NOT NULL,
    formato TEXT,
    categoria TEXT NOT NULL DEFAULT 'otros',
    tipo_documento TEXT NOT NULL DEFAULT 'desconocido',
    estado TEXT NOT NULL DEFAULT 'recibido',
    origen TEXT NOT NULL,
    gmail_mensaje_id TEXT,
    parte_id TEXT,
    remitente TEXT,
    asunto TEXT,
    fecha_correo TEXT,
    fecha_recepcion TEXT NOT NULL,
    fecha_documento TEXT,
    fecha_documento_manual INTEGER NOT NULL DEFAULT 0,
    num_paginas INTEGER,
    metodo_texto TEXT,
    duplicado_de INTEGER REFERENCES documentos(id),
    clasificacion_manual INTEGER NOT NULL DEFAULT 0,
    titular_manual INTEGER NOT NULL DEFAULT 0,
    marcado_incompleto INTEGER NOT NULL DEFAULT 0,
    notas TEXT NOT NULL DEFAULT '',
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_gmail_parte
    ON documentos(gmail_mensaje_id, parte_id) WHERE gmail_mensaje_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_doc_exp ON documentos(expediente_id);
CREATE INDEX IF NOT EXISTS ix_doc_sha ON documentos(sha256);

CREATE TABLE IF NOT EXISTS paginas_texto (
    documento_id INTEGER NOT NULL REFERENCES documentos(id),
    pagina INTEGER NOT NULL,
    texto TEXT NOT NULL,
    metodo TEXT NOT NULL,
    PRIMARY KEY (documento_id, pagina)
);

CREATE TABLE IF NOT EXISTS campos (
    id INTEGER PRIMARY KEY,
    expediente_id INTEGER NOT NULL REFERENCES expedientes(id),
    titular_id INTEGER REFERENCES titulares(id),
    clave TEXT NOT NULL,
    valor TEXT NOT NULL,
    estado TEXT NOT NULL,
    origen TEXT NOT NULL,
    documento_id INTEGER REFERENCES documentos(id),
    pagina INTEGER,
    evidencia TEXT NOT NULL DEFAULT '',
    nota TEXT NOT NULL DEFAULT '',
    creado TEXT NOT NULL,
    actualizado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_campos_exp ON campos(expediente_id);

CREATE TABLE IF NOT EXISTS requisitos (
    id INTEGER PRIMARY KEY,
    codigo TEXT UNIQUE NOT NULL,
    tipo_documento TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    ambito TEXT NOT NULL,
    operaciones TEXT NOT NULL DEFAULT '*',
    perfiles TEXT NOT NULL DEFAULT '*',
    minimo INTEGER NOT NULL DEFAULT 1,
    vigencia_dias INTEGER,
    controla_caducidad INTEGER NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS requisitos_expediente (
    expediente_id INTEGER NOT NULL REFERENCES expedientes(id),
    requisito_id INTEGER NOT NULL REFERENCES requisitos(id),
    modo TEXT NOT NULL,
    PRIMARY KEY (expediente_id, requisito_id)
);

CREATE TABLE IF NOT EXISTS incidencias (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'abierta',
    descripcion TEXT NOT NULL,
    expediente_id INTEGER REFERENCES expedientes(id),
    documento_id INTEGER REFERENCES documentos(id),
    gmail_mensaje_id TEXT,
    creado TEXT NOT NULL,
    resuelto TEXT,
    resolucion TEXT
);

CREATE TABLE IF NOT EXISTS mensajes (
    mensaje_id TEXT PRIMARY KEY,
    origen TEXT NOT NULL,
    estado TEXT NOT NULL,
    expediente_id INTEGER REFERENCES expedientes(id),
    remitente TEXT,
    asunto TEXT,
    fecha TEXT,
    motivo TEXT,
    intentos INTEGER NOT NULL DEFAULT 0,
    ultimo_error TEXT,
    procesado_en TEXT
);

CREATE TABLE IF NOT EXISTS informes (
    id INTEGER PRIMARY KEY,
    expediente_id INTEGER NOT NULL REFERENCES expedientes(id),
    version INTEGER NOT NULL,
    estado TEXT NOT NULL,
    creado TEXT NOT NULL,
    aprobado_en TEXT,
    ruta_docx TEXT,
    ruta_pdf TEXT,
    sha256_docx TEXT,
    sha256_pdf TEXT,
    datos_json TEXT NOT NULL,
    nota_aprobacion TEXT
);

-- Respuestas de la IA externa, por huella del texto enviado: reprocesar no reenvía datos.
CREATE TABLE IF NOT EXISTS cache_ia (
    huella TEXT PRIMARY KEY,
    modelo TEXT NOT NULL,
    respuesta_json TEXT NOT NULL,
    creado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actividad (
    id INTEGER PRIMARY KEY,
    fecha TEXT NOT NULL,
    tipo TEXT NOT NULL,
    expediente_id INTEGER,
    documento_id INTEGER,
    detalle TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_act_exp ON actividad(expediente_id);
"""


def ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def conectar(ruta_db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(ruta_db, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def transaccion(conn: sqlite3.Connection):
    """Transacción explícita (la conexión está en modo autocommit)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _migrar(conn: sqlite3.Connection) -> None:
    """Añade columnas nuevas a bases de datos creadas con versiones anteriores."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(expedientes)")}
    if "colaborador" not in columnas:
        conn.execute("ALTER TABLE expedientes ADD COLUMN colaborador TEXT NOT NULL DEFAULT 'Propio'")


def inicializar(conn: sqlite3.Connection) -> None:
    conn.executescript(ESQUEMA)
    _migrar(conn)
    conn.execute(
        "INSERT OR IGNORE INTO meta(clave, valor) VALUES ('version_esquema', ?)", (str(VERSION_ESQUEMA),)
    )
    from .checklist.requisitos import sembrar_requisitos_por_defecto

    sembrar_requisitos_por_defecto(conn)


def uno(conn: sqlite3.Connection, sql: str, params=()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def todos(conn: sqlite3.Connection, sql: str, params=()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()
