# CLAUDE.md

Gestor de expedientes hipotecarios: aplicación (PC Windows o servidor Ubuntu con Docker) para una intermediaria de crédito
inmobiliario en España. Automatiza recepción → almacenamiento → clasificación → renombrado →
checklist → extracción de datos → cálculos → borrador de informe. La usuaria solo interviene para
resolver incidencias, validar datos y aprobar el informe definitivo.

## Cómo trabajar con la usuaria

- **Idioma:** todo en español (código, interfaz, mensajes, documentación, commits).
- **Prioridad: la automatización.** Minimizar el trabajo manual.
- **Por fases:** implementa un bloque, explica cómo comprobarlo y **para**. No avances a la siguiente fase sin su confirmación.
- **Despliegue objetivo: su servidor (Contabo, Ubuntu + Docker)**, con acceso privado por Tailscale y Google Drive como entrada y copia (ver `DESPLIEGUE.md`). El modo PC Windows sigue funcionando.
- **Portabilidad:** debe poder cambiar de PC, de cuenta de Google (Gmail y Drive son independientes) y de servidor solo con configuración. Nada específico de una cuenta o máquina en el código.
- **Una sola usuaria.** No implementar cuentas, roles ni multiusuario. Los «colaboradores» (otros brókers) son solo un atributo del expediente que agrupa carpetas y filtra vistas.
- **Protección de datos:** la usuaria considera sobreingeniería, por ahora, cualquier evaluación RGPD o documento legal. Mantén las salvaguardas técnicas existentes, pero no propongas más trabajo en esa línea.
- **Datos de prueba:** solo ficticios o anonimizados (`ejemplos/`, «Lucía Prueba Ficticia», DNI 12345678Z).
- **Su `config.toml` y su carpeta `datos/`:** no los modifiques; pueden contener su configuración y pruebas. Si un cambio requiere ajustar `config.toml`, díselo.

## Comandos

```powershell
.\.venv\Scripts\python.exe -m pytest -q                       # pruebas (deben pasar todas)
.\.venv\Scripts\python.exe -m hipotecas --sin-navegador        # arrancar (http://127.0.0.1:8765)
.\.venv\Scripts\python.exe -m hipotecas --config RUTA\config.toml   # con otra configuración (pruebas manuales)
.\.venv\Scripts\python.exe scripts\generar_documentos_ficticios.py --referencia EXP-2026-0001
```

Para probar en vivo usa una configuración temporal con otro puerto y otra carpeta de datos; nunca la de la usuaria.

Servidor: `bash despliegue/instalar_servidor.sh`, `docker compose up -d --build`, `docker compose logs -f gestor|drive`.
Docker Desktop no arranca en su PC: la imagen no se ha podido construir localmente; valida con `docker compose config` en una copia temporal.

## Arquitectura (paquete `hipotecas/`)

| Módulo | Responsabilidad |
|---|---|
| `config.py` | TOML con valores por defecto (`DEFECTO`). Rutas: `dir_datos`, `dir_gestor`, `dir_entrada`, `dir_expedientes`, `dir_incidencias`. |
| `db.py` | Esquema SQLite (`CREATE IF NOT EXISTS`) + `_migrar()` para columnas nuevas. `transaccion()` explícita (conexión en autocommit). |
| `almacenamiento.py` | Carpetas de expediente, originales inmutables, copias de trabajo. Rutas en BD **relativas a `dir_datos`**. |
| `seguridad.py` | `sanear_nombre`, `ruta_segura`, `resolver_relativa`, `ruta_unica`, `escribir_exclusivo`. |
| `ingesta.py` | Punto común de entrada (`Mensaje`/`Adjunto` → `procesar_mensaje`): Gmail, `.eml` y carpeta de entrada. Asociación a expediente e incidencias. |
| `carpeta_entrada.py` | Vigilante de `GESTOR HIPOTECARIO\00 - ENTRADA` (hilo con sondeo). |
| `gmail/` | OAuth `gmail.readonly`, token cifrado con DPAPI, cliente y sincronizador periódico. |
| `documentos/` | `texto.py` (formato real, texto PDF, OCR Tesseract local) y `procesamiento.py` (`registrar` → `procesar` → `analizar`). |
| `extraccion/` | `ProveedorExtraccion` (interfaz), `anthropic_proveedor.py` (única implementación), `clave_api.py`, `servicio.py`. |
| `datos.py` | Datos con estado (`pendiente_confirmar`, `acreditado`, `declarado`, `calculado`, `descartado`) y trazabilidad (documento, página, evidencia). |
| `checklist/` | Requisitos configurables (tabla `requisitos`, semilla en JSON), vigencias y coherencia. |
| `finanzas.py` | Cálculos deterministas con `Decimal` (cuota francesa, endeudamiento, LTV, aportación). |
| `situacion.py` | Reúne datos y cálculos para la pantalla y el informe. |
| `informes/` | Contenido serializable → DOCX/PDF; borrador y aprobación explícita. |
| `catalogo.py` | Tipos de operación, perfiles, tipos de documento y campos, **con las descripciones que se usan como instrucciones para la IA**. |
| `web/` | FastAPI + Jinja2; `rutas.py` y `templates/`. |
| `copias.py` | Copia diaria de la BD en `datos/copias` (se sube a Drive en el servidor). |
| `Dockerfile`, `docker-compose.yml`, `despliegue/` | Imagen con Tesseract en español; servicios `gestor` y `drive` (rclone en bucle: `sincronizar_drive.sh`); instalación y restauración. Lo propio del servidor vive en `servidor/` (en `.gitignore`). |

## Invariantes (no romper)

- **Nunca expuesto a Internet.** En el PC escucha en 127.0.0.1. En Docker (`HIPOTECAS_CONTENEDOR=1`) escucha en 0.0.0.0 dentro del contenedor, pero compose solo publica `127.0.0.1:8765` y el acceso es por `tailscale serve`. El middleware `ProteccionLocal` valida Host y Origin/Referer (http/https) contra loopback y `[servidor] hosts_permitidos`.
- **Secretos:** en Windows, cifrados con DPAPI; en Linux, archivo con permisos 600 en el volumen. Gmail se autoriza con `AutorizacionGmail` (redirección a `http://127.0.0.1:<puerto>/`: en el PC vuelve sola; en el servidor se pega la URL). No se copian a Drive.
- **Drive:** `00 - ENTRADA` va de Drive al servidor (`rclone move`); `02`, `03` y las copias de la BD van del servidor a Drive (`rclone sync` con `--backup-dir .historial`). En Drive esas carpetas son de solo consulta.
- **Interfaz:** la CSP es estricta: **sin JavaScript ni estilos en línea** (`style=`); usa clases de `static/estilo.css`. Los formularios no pueden redirigir a otros dominios (`form-action 'self'`): para ir a Google se muestra un enlace.
- **Originales:** se escriben en modo exclusivo, quedan en solo lectura y nunca se sobrescriben ni se borran. Las correcciones solo mueven o renombran la copia de trabajo.
- **Nunca se borran documentos automáticamente.** Única excepción: un archivo de `00 - ENTRADA` se retira solo **después** de verificar por SHA-256 que está guardado en su destino; los duplicados se mueven a `03 - INCIDENCIAS\Entrada duplicada`.
- **Idempotencia:** `mensajes.mensaje_id` y el índice único `(gmail_mensaje_id, parte_id)`. En la carpeta de entrada, `mensaje_id = carpeta-<sha>-<subcarpeta>`.
- **No adivinar:** la asociación dudosa va a incidencias; el titular solo se asigna si es inequívoco; sin IA el documento queda `pendiente_analisis`.
- **Extracción solo con IA, sin reglas** (se eliminaron a petición de la usuaria; no reintroducirlas). La IA solo propone datos `pendiente_confirmar`, y cada dato exige una cita literal comprobada en el texto enviado. Nunca decide créditos ni aprueba informes.
- **IA:** se envía solo el texto, con IBAN, correos y teléfonos seudonimizados. Se activa con `[ia] permitir_envio_externo = true` y una clave (variable de entorno o DPAPI desde la pantalla IA). Modelo por defecto `claude-opus-5-5`, con salida de esquema JSON, `fallbacks="default"` y caché en la tabla `cache_ia`.
- **Cálculos:** siempre deterministas (`Decimal`), nunca a cargo del modelo.
- **Informes:** nunca se envían a terceros. Aprobar exige marcar la casilla y escribir la referencia; los finales se generan desde la misma instantánea `datos_json`.
- **Logs** (`datos/logs`): solo identificadores y tipos de evento, nunca contenido de documentos. Las acciones relevantes se registran con `actividad.registrar()`.
- **Compatibilidad:** los expedientes antiguos tienen `carpeta` sin «/» y viven en `datos/expedientes`; los nuevos guardan una ruta relativa. Las columnas nuevas se añaden en `db._migrar()`.

## Pruebas

- `tests/simulador_ia.py`: cliente de IA simulado (sin red). Las fixtures `proveedor_ia` y `cliente_ia` están en `conftest.py`. En la web, se sustituye `app.state.proveedor`.
- `tests/test_gmail.py`: servicio Gmail falso.
- La IA real **no se ha probado** contra la API (no había clave). Pendiente de validar con la usuaria desde la pantalla IA.
- Ante cada cambio: añadir pruebas y ejecutar toda la batería.

## Estado y siguientes fases (pendientes de confirmación de la usuaria)

Hecho: despliegue Docker + Drive + Tailscale preparado (pendiente de que la usuaria lo ejecute en su servidor); recepción automática (Gmail con etiqueta, remitentes de expedientes y búsqueda configurable; carpeta de entrada), estructura `GESTOR HIPOTECARIO`, colaboradores, extracción con IA, checklist, cálculos, informes e interfaz.

Pendiente, por orden propuesto:
1. Ampliar el catálogo para la IA:
   - Operaciones: inversión, reunificación de deudas.
   - Documentos: certificado de retenciones, más documentación de autónomos.
   - Campos: estado civil, personas a cargo, residencia, jornada, profesión, ingresos variables y anuales, finalidad, otros compromisos.
2. Escenarios comparativos de plazo, tipo y porcentaje de financiación; criterios de análisis y vigencias por banco.
3. Borrador de informe automático cuando haya datos suficientes; plantillas personalizables.
