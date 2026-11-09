# Gestión local de expedientes hipotecarios

Aplicación local para una sola usuaria (intermediaria de crédito inmobiliario) que automatiza:

1. **Recepción automática**: desde Gmail (etiqueta, remitentes de tus expedientes o búsqueda configurable) y desde la **carpeta de entrada** `GESTOR HIPOTECARIO\00 - ENTRADA` (p. ej. documentos de WhatsApp).
2. **Gestión documental** por expediente: originales inalterables, huella SHA-256, duplicados, copias de trabajo renombradas por categoría.
3. **Extracción de texto** (PDF digital y OCR local) y **clasificación y extracción de datos con IA**, con documento, página y cita de origen.
4. **Checklist documental configurable** por tipo de operación y perfil laboral, con vigencias.
5. **Cálculos deterministas**: cuota francesa, endeudamiento, LTV, financiación y aportación de fondos.
6. **Borradores de informe** en DOCX y PDF, que solo pasan a definitivos con **aprobación explícita**.

Todo se ejecuta y se guarda en tu ordenador. El servidor solo escucha en `127.0.0.1` (no es accesible desde la red local ni desde Internet). Nada se envía a bancos ni a terceros.

---

## Índice

- [Requisitos](#requisitos)
- [Instalación](#instalación)
- [Arranque](#arranque) · [Arranque automático](#arranque-automático-al-iniciar-sesión)
- [Configuración](#configuración)
- [Carpeta de entrada](#carpeta-de-entrada-whatsapp-y-otros)
- [Conectar Gmail (OAuth)](#conectar-gmail-oauth) ← **pasos que debes hacer tú**
- [Activar la IA](#activar-la-ia) ← **pasos que debes hacer tú**
- [OCR para documentos escaneados](#ocr-para-documentos-escaneados-opcional)
- [Uso diario](#uso-diario)
- [Checklist documental](#checklist-documental)
- [Datos y estados](#datos-extraídos-y-su-estado)
- [Informes: generación, aprobación y exportación](#informes-generación-aprobación-y-exportación)
- [IA y protección de datos](#ia-y-protección-de-datos)
- [Seguridad](#seguridad)
- [Pruebas](#pruebas-automáticas) · [Comprobación manual con datos ficticios](#comprobación-manual-del-flujo-con-datos-ficticios)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Limitaciones conocidas](#limitaciones-conocidas)

---

## Requisitos

- Windows 10/11.
- Python 3.11 o superior (probado con 3.14). Descarga: <https://www.python.org/downloads/windows/> (marca «Add python.exe to PATH»).
- Opcional: Tesseract OCR con idioma español, para documentos escaneados.
- Una clave de API de Anthropic (<https://console.anthropic.com/>) para la clasificación y extracción con IA.
- Opcional: una cuenta de Google Cloud (gratuita) para crear las credenciales OAuth de Gmail.

## Instalación

Abre PowerShell en la carpeta del proyecto y ejecuta:

```powershell
powershell -ExecutionPolicy Bypass -File .\instalar.ps1
```

El script crea el entorno virtual `.venv`, instala dependencias, crea `config.toml` a partir de `config.example.toml`, genera documentos ficticios de prueba y ejecuta las pruebas automáticas.

Instalación manual equivalente:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy config.example.toml config.toml
```

## Arranque

Doble clic en **`iniciar.bat`** (o `.\.venv\Scripts\python.exe -m hipotecas`). Se abre el navegador en <http://127.0.0.1:8765/>.

- Cerrar: `Ctrl+C` en la ventana negra (o cerrar la ventana).
- Si la aplicación ya está en marcha, `iniciar.bat` solo abre el navegador.
- Opciones: `--sin-navegador`, `--config RUTA\config.toml`.

### Arranque automático al iniciar sesión

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\arranque_automatico.ps1 -Activar
```

Crea un acceso directo en la carpeta *Inicio* de tu usuario que lanza la aplicación en segundo plano (sin ventana). Después solo tienes que abrir <http://127.0.0.1:8765/> (añádelo a favoritos). Para desactivarlo: `-Desactivar`.

Al arrancar, la aplicación **recupera automáticamente** los correos etiquetados que quedaron pendientes (por ejemplo, si el ordenador estuvo apagado) y luego sincroniza cada `intervalo_minutos`.

## Configuración

Todo se ajusta en `config.toml` (excluido del control de versiones). Las secciones principales:

| Sección | Para qué |
|---|---|
| `[servidor]` | Puerto (por defecto 8765). El host **solo** puede ser de loopback; con otro valor la aplicación no arranca. |
| `[carpetas]` | Nombres de `GESTOR HIPOTECARIO`, `00 - ENTRADA`, `02 - EXPEDIENTES`, `03 - INCIDENCIAS`; frecuencia de revisión de la entrada. |
| `[rutas] datos` | Carpeta de expedientes, base de datos, tokens y registros. Recomendado: una carpeta fuera del proyecto en un disco cifrado con BitLocker, p. ej. `'C:\Users\TU_USUARIO\Documents\Hipotecas'`. |
| `[expedientes.subcarpetas]` | Nombres de las carpetas por categoría (identificación, laboral, económica/fiscal, endeudamiento, vivienda, informes, otros). |
| `[documentos]` | Tamaño máximo, extensiones admitidas (PDF, JPG, PNG, TIFF). |
| `[ocr]` | Ruta de `tesseract.exe` e idioma. |
| `[gmail]` | Activación, etiqueta, intervalo, reglas de asociación. |
| `[ia]` | Extracción con IA: permiso de envío, modelo, esfuerzo, límite de texto, seudonimización. |
| `[calculos]` | Tipo de interés, plazo, umbrales de endeudamiento/LTV/financiación, impuestos y gastos estimados, escenario de estrés. |

Reinicia la aplicación tras cambiar `config.toml`. Los valores de `[calculos]` son supuestos por defecto: en cada expediente puedes fijar importe, plazo, tipo, ahorros, impuestos y gastos como datos propios (pestaña *Datos extraídos* → *Añadir dato*).

Estructura de carpetas (dentro de la carpeta de datos):

```
GESTOR HIPOTECARIO\
  00 - ENTRADA\
    EXP-2026-0001 - Nombre\     ← deja aquí los documentos de ese expediente (se crea sola)
  02 - EXPEDIENTES\
    <Colaborador>\              ← «Propio» o el bróker con el que colaboras
      EXP-2026-0001_Nombre\     ← estructura del expediente (abajo)
  03 - INCIDENCIAS\             ← lo que no se ha podido asociar con seguridad
    Entrada duplicada\          ← archivos depositados dos veces (no se reprocesan)
```

Los expedientes creados con la versión anterior siguen en `datos\expedientes\` y funcionan igual.
El estado «pendiente de revisión» no es una carpeta: se ve en el *Panel* y en cada expediente.

Estructura de cada expediente:

```
EXP-2026-0001_Nombre\
  00_Entrada_Originales\   ← originales tal como llegaron (solo lectura, nunca se modifican)
  01_Identificacion\       ← copias de trabajo renombradas: TIPO_TITULAR_AAAAMM_docN.pdf
  02_Laboral\
  03_Economica_Fiscal\
  04_Endeudamiento\
  05_Vivienda\
  06_Informes\             ← borradores y versiones finales
  07_Otros\
```

## Carpeta de entrada (WhatsApp y otros)

La aplicación revisa `GESTOR HIPOTECARIO\00 - ENTRADA` cada 15 segundos (configurable en `[carpetas]`). Lo que dejes ahí se procesa **exactamente igual que un adjunto de correo**: se guarda el original, se calcula la huella, se detectan duplicados, la IA lo clasifica y extrae sus datos, y se renombra la copia de trabajo.

- **En la subcarpeta del expediente** (`00 - ENTRADA\EXP-2026-0001 - Nombre\`): se asocia a ese expediente. Cada expediente abierto tiene su subcarpeta, creada automáticamente.
- **Suelto en `00 - ENTRADA`**: se asocia solo si el nombre del archivo contiene la referencia (p. ej. `EXP-2026-0001 nómina.pdf`); si no, va a *Incidencias* para que decidas.
- **Cuando el documento queda guardado y verificado** (misma huella SHA-256) en el expediente o en incidencias, el archivo se retira de la entrada. Si algo falla, se queda donde está y se reintenta.
- **Archivos a medio copiar** (modificados hace menos de 5 s), temporales (`~$…`, `.crdownload`, `.tmp`…) o bloqueados por otro programa: se esperan.
- **El mismo archivo depositado dos veces**: no se reprocesa; se mueve a `03 - INCIDENCIAS\Entrada duplicada`.
- Pantalla **Entrada**: ruta, archivos pendientes, resultado de la última revisión y botón *Revisar ahora*.

## Conectar Gmail (OAuth)

> Estos pasos requieren tu cuenta de Google y no puedo hacerlos por ti. Llevan unos 10 minutos y se hacen una sola vez.

La aplicación usa el permiso **`gmail.readonly`**: puede leer mensajes y adjuntos, pero **no puede enviar, borrar ni modificar** correos (ni siquiera quitar etiquetas). Solo consulta los mensajes que tienen la etiqueta configurada; nunca recorre el resto del buzón. Los mensajes ya procesados se recuerdan en la base de datos local para no repetirlos.

1. **Crea un proyecto** en <https://console.cloud.google.com/> (p. ej. «Hipotecas local»).
2. **Habilita la API de Gmail**: *APIs y servicios → Biblioteca → Gmail API → Habilitar*.
3. **Configura la pantalla de consentimiento** (*Google Auth Platform* / *Pantalla de consentimiento de OAuth*):
   - Tipo de usuario: **Interno** si usas Google Workspace; **Externo** si es una cuenta @gmail.com.
   - Nombre de la aplicación y tu correo de contacto.
   - Permisos (*Acceso a datos / Scopes*): añade `https://www.googleapis.com/auth/gmail.readonly`.
   - Si es Externo: en *Público / Usuarios de prueba* añade tu propia dirección de Gmail.
4. **Crea el ID de cliente**: *Credenciales → Crear credenciales → ID de cliente de OAuth → Tipo: Aplicación de escritorio*. Descarga el JSON.
5. **Guarda el JSON** como `datos\secretos\credentials.json` (dentro de la carpeta `datos` configurada). Esta carpeta está excluida del control de versiones.
6. **Crea la etiqueta en Gmail**: `Hipotecas` y, dentro, `Procesar` (queda como `Hipotecas/Procesar`). Opcional: un filtro de Gmail que la aplique automáticamente a ciertos remitentes; o aplícala a mano a los correos que quieras procesar.
7. En `config.toml` pon `[gmail] habilitado = true` y reinicia la aplicación.
8. En la aplicación: **Entrada → Conectar Gmail**. Se abrirá el navegador; inicia sesión y acepta. Google mostrará «Google no ha verificado esta aplicación»: es normal en una aplicación propia; pulsa *Continuar*.
9. Pulsa **Sincronizar ahora** para la primera lectura.

**Dónde queda el token:** `datos\secretos\gmail_token.bin`, cifrado con **DPAPI de Windows** (solo tu usuario de Windows en este equipo puede descifrarlo). *Desconectar* lo elimina; para revocar también el permiso en Google: <https://myaccount.google.com/permissions>.

**Caducidad en modo prueba:** con tipo de usuario *Externo* en estado *Prueba*, Google caduca la autorización a los 7 días. La pantalla *Entrada* lo indicará («vuelve a conectar») y basta con pulsar *Reconectar*. Con Google Workspace (tipo *Interno*) no ocurre.

### Cómo se asocia un correo a un expediente

1. Si el **asunto** contiene **una** referencia existente (p. ej. `Documentación [EXP-2026-0001]`) → ese expediente. Si además el remitente es contacto de *otro* expediente y no de este, se considera conflicto → incidencias.
2. Si no hay referencia y el **remitente** coincide exactamente con el correo de contacto de **un único** expediente abierto → ese expediente (se puede desactivar con `asociar_por_remitente = false`).
3. En cualquier otro caso (sin referencia ni remitente conocido, varias referencias, referencia inexistente, expediente cerrado, remitente en varios expedientes) → **bandeja de incidencias**. Los adjuntos se guardan igualmente y puedes asignarlos a mano.

Consejo: pide a tus clientes que incluyan la referencia del expediente en el asunto.

### Qué correos se recogen

No se recorre la bandeja completa. Se procesan:

1. Los mensajes con la **etiqueta** configurada (`Hipotecas/Procesar`).
2. Con `incluir_remitentes_de_expedientes = true` (por defecto): los correos **con adjuntos** de los **correos de contacto de tus expedientes abiertos** (y de sus titulares), de los últimos `dias_remitentes` días. Así no tienes que etiquetar nada: basta con registrar el correo del cliente al crear el expediente.
3. Opcional: una **búsqueda de Gmail** propia en `consulta` (p. ej. `has:attachment subject:(hipoteca OR documentación) newer_than:15d`).

## Activar la IA

La clasificación de documentos y la extracción de datos se hacen **exclusivamente con IA** (Anthropic). No hay reglas fijas: la IA lee el texto de cada documento, decide su tipo y extrae los datos con su página y una cita literal, que la aplicación verifica antes de aceptarla.

1. Crea una clave de API en <https://console.anthropic.com/> (*API Keys*). El uso se factura por consumo.
2. En `config.toml`, bloque `[ia]`, pon `permitir_envio_externo = true` (es tu consentimiento explícito para enviar texto fuera del ordenador). Si tu `config.toml` es anterior a este cambio y tiene `proveedor = "local"`, sustituye el bloque `[ia]` completo por el de `config.example.toml`.
3. Reinicia la aplicación y abre la pantalla **IA**: pega la clave en *Guardar clave* (se guarda cifrada con DPAPI y no se vuelve a mostrar). Alternativa: variable de entorno `ANTHROPIC_API_KEY`.
4. Pulsa **Enviar prueba**: manda un texto de nómina inventado y muestra el tipo y los datos detectados.
5. Si ya tenías documentos *pendientes de análisis*, pulsa **Analizar pendientes**.

**Mientras la IA no esté activada (o si falla):** los documentos se reciben, se guardan y se extrae su texto igualmente, pero quedan **«Pendiente de análisis»**, sin clasificar y sin datos. Puedes clasificarlos y anotar los datos a mano, o esperar: se reintentan con *Analizar pendientes*, con *Reprocesar* en cada documento y automáticamente en cada sincronización de Gmail. Si la IA falla con un documento concreto, se abre una incidencia «No se pudo analizar con IA». Nunca se clasifica ni se rellena nada por suposición.

Ajustes en `[ia]`: `modelo` (por defecto `claude-opus-5-5`), `esfuerzo` (`low`/`medium`/`high`), `max_caracteres_texto` (límite por documento), `seudonimizar`.

## OCR para documentos escaneados (opcional)

Los PDF digitales no lo necesitan. Para PDF escaneados e imágenes:

1. Instala Tesseract para Windows (instalador de UB Mannheim: <https://github.com/UB-Mannheim/tesseract/wiki>). Durante la instalación, en *Additional language data*, marca **Spanish**.
2. Comprueba la ruta en `config.toml` → `[ocr] tesseract_cmd` (por defecto `C:\Program Files\Tesseract-OCR\tesseract.exe`).
3. Reinicia. El *Panel* mostrará «OCR: disponible».

Sin OCR, los escaneados se guardan igual y generan una incidencia visible «Requiere OCR»; puedes clasificarlos y anotar los datos a mano, o pulsar *Reprocesar* tras instalar Tesseract. El OCR es 100 % local.

## Uso diario

1. **Panel**: incidencias, documentos pendientes de revisión y de análisis con IA, datos por confirmar y estado de Gmail/OCR/IA.
2. **Expedientes → Nuevo**: nombre, tipo de operación, **colaborador** (vacío = «Propio»), correos de contacto del cliente y primer titular. Puedes añadir más titulares en *Resumen*. El listado se puede filtrar por colaborador.
3. **Documentos**: llegan solos por Gmail o por la carpeta de entrada; también puedes importar un `.eml` (pantalla *Entrada*) o subirlos en la pestaña *Documentos*. En la ficha de cada documento puedes ver el original, la copia de trabajo y el texto extraído, y **corregir tipo, titular, fecha, estado** (revisado / descartado) o marcarlo como **incompleto**. Corregir solo afecta a la copia de trabajo; el original no se toca. *Descartado* no borra nada: solo deja de contar.
4. **Datos extraídos**: revisa cada dato con su documento, página y fragmento de evidencia → *Confirmar*, *Corregir* o *Descartar*. Añade datos declarados por el cliente (importe solicitado, ahorros, plazo…).
5. **Checklist**: requisitos aplicables y su estado. Puedes marcar requisitos como *Obligatorio* o *No aplica* solo para este expediente.
6. **Cálculos**: simulación, ratios y aportación de fondos, con la fuente y el estado de cada dato de entrada.
7. **Informes**: escribe las observaciones, genera el borrador, revísalo y apruébalo.
8. **Incidencias**: asigna correos dudosos a un expediente, marca resueltas o descarta.
9. **IA**: estado, clave, prueba, análisis de pendientes y registro de cada envío.
10. **Actividad**: registro de todo lo ocurrido (sin contenido de documentos).

## Checklist documental

Pantalla **Checklist** (menú superior). Cada requisito tiene:

| Campo | Significado |
|---|---|
| Código | Identificador único (`NOMINAS`, `CIRBE`…). |
| Tipo de documento | Qué documento lo satisface (DNI acepta también NIE). |
| Ámbito | *Por titular* (se exige a cada titular) o *Por operación*. |
| Operaciones | `*` o lista de códigos: `compra_vivienda_habitual, compra_segunda_vivienda, subrogacion_cambio_banco, refinanciacion, autopromocion, otra`. |
| Perfiles | `*` o lista: `asalariado_indefinido, asalariado_temporal, funcionario, autonomo, pensionista, otro`. |
| Mínimo | Número de documentos vigentes necesarios (p. ej. 3 nóminas). |
| Vigencia | Antigüedad máxima en días según la fecha del documento. |
| Caducidad | Para documentos de identidad: comprueba la fecha de validez extraída. |
| Activo | Si está inactivo, solo se exige en los expedientes donde lo marques como *Obligatorio*. |

Estados posibles: **Correcto, Pendiente, Antiguo, Incompleto, Caducado, Fecha no determinada, Incoherente**. Un documento **nunca** aparece como pendiente si ningún requisito activo y aplicable lo exige. Además se señalan duplicados, documentos sin titular, sin clasificar, con incidencia e incoherencias (nombre o DNI que no coinciden con el titular, valores contradictorios, líquidos de nómina muy dispares, documento de identidad caducado…).

El conjunto inicial está en `hipotecas/checklist/requisitos_por_defecto.json` (se carga la primera vez). *Exportar JSON* descarga la configuración actual.

## Datos extraídos y su estado

| Estado | Significado |
|---|---|
| **Pendiente de confirmar** | Extraído automáticamente; aún no lo has revisado. |
| **Acreditado** | Confirmado por ti y respaldado por un documento (con nº de documento y página). |
| **Declarado** | Comunicado por el cliente, sin justificante. |
| **Calculado** | Derivado de otros datos (p. ej. media de nóminas). Si alguna entrada está sin confirmar, se indica. |
| Supuesto de configuración | Valor por defecto de `config.toml` (tipo, plazo, impuestos, gastos) cuando el expediente no tiene uno propio. |

La extracción nunca rellena datos que no encuentra. Si un dato falta, aparece como «Sin dato» en cálculos e informe y como cuestión por confirmar.

## Informes: generación, aprobación y exportación

1. Pestaña **Informes → Generar nuevo borrador**. Se crean `Informe_EXP-…_vN_BORRADOR.docx` y `.pdf` en `06_Informes`, marcados como *BORRADOR — No distribuir*. Cada generación es una versión nueva; nada se sobrescribe.
2. Descarga el DOCX o PDF desde la tabla de versiones y revísalo (puedes abrir el DOCX en Word para tu propia edición).
3. **Aprobar**: solo la última versión, marcando «He revisado el borrador…» y **escribiendo la referencia del expediente**. Se generan `…_vN_FINAL.docx` y `.pdf` a partir de **exactamente el mismo contenido que revisaste**, como solo lectura y con su huella SHA-256 registrada. La IA no puede aprobar informes: solo esta acción manual lo hace.
4. El informe incluye: datos del expediente, situación laboral y económica por titular, endeudamiento, operación, simulación, ratios y aportación, observaciones, indicadores que superan umbrales, documentación pendiente, cuestiones por confirmar y anexo de documentos aportados. Cada dato muestra su estado y su fuente. No lleva marca comercial.

La aplicación **no envía** informes a nadie. Para compartirlos, adjunta tú misma el archivo final.

## IA y protección de datos

- **Activación explícita:** sin `permitir_envio_externo = true` y una clave de API no sale ningún texto del ordenador (los documentos quedan pendientes de análisis).
- **No uses documentos reales durante el desarrollo.** Solo los ficticios de `ejemplos\`.
- **Qué sale del ordenador, por documento:** el texto extraído de sus páginas (hasta `max_caracteres_texto`) con destino a la API de Anthropic. Con `seudonimizar = true` (por defecto) los **IBAN, correos electrónicos y teléfonos** se sustituyen por marcadores antes de enviar, porque no se necesitan. Si has fijado el tipo a mano, también se envía ese tipo.
- **Qué no sale nunca:** archivos ni imágenes, nombres de archivo, remitentes, asuntos, referencias de expediente, datos de los titulares registrados ni datos de otros documentos. El OCR es local.
- **Trazabilidad:** cada envío queda registrado (pantalla *IA* y *Actividad*, evento `extraccion_ia_externa`): documento, modelo, páginas y nº de caracteres. Las respuestas se guardan en una caché local por huella del texto, así que reprocesar el mismo documento no vuelve a enviarlo (evento `extraccion_ia_cache`). Si el modelo rechaza una petición, la API de Anthropic puede reintentarla en otro modelo de Anthropic (`usar_fallback_servidor`); los datos no salen de ese proveedor.
- **Control de calidad:** salida con esquema JSON cerrado; cada dato debe ir con una cita literal que se comprueba en el texto enviado, y los importes deben aparecer en esa cita. Lo que no se puede verificar se descarta y se avisa. Todo queda *pendiente de confirmar* hasta que lo revises.
- **Límites de la IA:** no toma decisiones crediticias, no valora la solvencia y no puede aprobar informes.
- **Cambio de proveedor:** la interfaz `ProveedorExtraccion` (`hipotecas/extraccion/base.py`) permite añadir otro proveedor sin tocar el resto de la aplicación.
- Antes de usarlo con datos reales, revisa tus obligaciones (RGPD, contrato de encargo de tratamiento con el proveedor, información a tus clientes).
- **Gmail:** si lo habilitas, la conexión con Google es de solo lectura.

## Seguridad

- Servidor solo en `127.0.0.1`; la configuración rechaza cualquier otra dirección.
- Protección frente a *DNS rebinding* (cabecera Host) y peticiones desde otras webs (Origin/Referer en toda acción que modifica datos). Cabeceras CSP estrictas, sin JavaScript.
- Rutas de archivo saneadas y verificadas contra *path traversal*; los archivos se crean en modo exclusivo (nunca se sobrescriben). Originales en solo lectura.
- **No se borran documentos automáticamente**; «descartar» solo los marca.
- Los registros técnicos (`datos\logs\aplicacion.log`) solo contienen identificadores y tipos de evento, nunca el contenido de documentos.
- Token de Gmail cifrado con DPAPI. `.gitignore` excluye `datos/`, `config.toml`, credenciales, tokens, bases de datos, logs y documentos (salvo los ficticios de `ejemplos/`).
- Diseñado para una sola usuaria: sin cuentas, roles ni multiusuario. Protege tu sesión de Windows con contraseña y activa **BitLocker**. Haz copias de seguridad cifradas de la carpeta `datos`.

## Pruebas automáticas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Cubren cálculos financieros, seguridad de rutas y configuración, cifrado del token y de la clave de API, la extracción con IA mediante un cliente simulado (sin red: verificación de citas, descarte de datos inventados, caché, seudonimización, fallos y reintentos), el flujo completo (correos, asociación, duplicados, incidencias, checklist, cálculos, informe y aprobación), el conector de Gmail con un servicio simulado (solo etiqueta, sin reprocesar, reintentos) y la interfaz web (incluidas las protecciones locales).

## Comprobación manual del flujo con datos ficticios

> Usa **solo** los documentos ficticios de `ejemplos\` (persona inventada «Lucía Prueba Ficticia», DNI de prueba 12345678Z).

### A. Sin Gmail (5 minutos)

1. Arranca con `iniciar.bat` y [activa la IA](#activar-la-ia) (sin ella podrás comprobar todo salvo la clasificación y los datos extraídos: los documentos quedarán «pendientes de análisis»).
2. *Expedientes → Nuevo*: nombre «Prueba ficticia», tipo *Compra de vivienda habitual*, correo de contacto `lucia.prueba@ejemplo.invalid`, titular «Lucía Prueba Ficticia», *Asalariado indefinido*, DNI `12345678Z`. Anota la referencia (p. ej. `EXP-2026-0001`).
3. Regenera los correos con esa referencia:
   `.\.venv\Scripts\python.exe scripts\generar_documentos_ficticios.py --referencia EXP-2026-0001`
4. *Entrada → Importar .eml*, en este orden, los archivos de `ejemplos\correos_ficticios\`:
   - `1_con_referencia.eml` → se asocia por la referencia del asunto (DNI, 3 nóminas, contrato, vida laboral).
   - `2_desde_remitente_registrado.eml` → se asocia por el remitente (IRPF, extracto, préstamo, arras, nota simple).
   - `3_remitente_desconocido.eml` → va a **Incidencias** (no se adivina).
   - `4_duplicado_y_errores.eml` → DNI **duplicado**, `.txt` **no admitido**, PDF **dañado** e imagen que **requiere OCR** (si no tienes Tesseract).
   - Importa otra vez `1_con_referencia.eml` → «ya se había procesado» (no duplica).
5. Comprueba en el expediente: carpetas en `datos\expedientes\EXP-…\` (originales en `00_Entrada_Originales`, copias renombradas en cada categoría); pestaña *Documentos* con procedencia y tipo detectado por la IA; *Checklist* con **CIRBE pendiente** y sin requisitos de pensión ni autónomo; *Datos extraídos* pendientes de confirmar con documento y página.
6. Abre un documento, revisa y pulsa *Confirmar todos los pendientes*. Corrige un tipo o titular y verifica que la copia de trabajo cambia de nombre/carpeta y el original no.
7. En *Datos extraídos → Añadir dato*: «Importe de préstamo solicitado» = `190.000` (declarado) y «Ahorros aportados» = `60.000` (declarado). Mira *Cálculos*.
8. *Incidencias*: asigna el correo del remitente desconocido al expediente (el extracto aparecerá como duplicado, porque ya se recibió). Marca resueltas las demás.
9. **Carpeta de entrada:** copia `ejemplos\documentos_ficticios\nota_simple.pdf` en `GESTOR HIPOTECARIO\00 - ENTRADA\EXP-… - Prueba ficticia\` y otro PDF suelto en `00 - ENTRADA\`. En unos segundos el primero aparece en el expediente (procedencia «Carpeta de entrada») y el segundo en *Incidencias*; los dos desaparecen de la entrada. Copia otra vez el mismo PDF en la subcarpeta: acaba en `03 - INCIDENCIAS\Entrada duplicada`.
10. *Informes*: escribe observaciones, genera el borrador, descarga DOCX y PDF. Intenta aprobar con una referencia incorrecta (debe rechazarlo); después apruébalo correctamente y comprueba los archivos `_FINAL` en `06_Informes`.

### B. Con un buzón de Gmail de pruebas

1. Crea una cuenta de Gmail **solo para pruebas** (no la profesional) y sigue [Conectar Gmail](#conectar-gmail-oauth) con ella.
2. Desde otra cuenta, envía a ese buzón un correo con asunto `Documentación [EXP-2026-0001]` y adjunta 2–3 PDF de `ejemplos\documentos_ficticios\`. Envía otro sin referencia desde una dirección no registrada.
3. En el buzón de pruebas aplica la etiqueta `Hipotecas/Procesar` a ambos. Deja un tercer correo **sin** etiqueta.
4. *Entrada → Sincronizar ahora*. El primero aparece en el expediente con remitente, fecha, asunto e ID de mensaje; el segundo en *Incidencias*; el tercero se ignora.
5. Cierra la aplicación, etiqueta un correo nuevo y vuelve a arrancarla: se procesa al iniciar (recuperación tras reinicio). Pulsa *Sincronizar ahora* otra vez: no se duplica nada.

## Estructura del proyecto

```
hipotecas/
  __main__.py            arranque (valida loopback, abre el navegador)
  config.py              configuración TOML con valores seguros por defecto
  db.py                  esquema SQLite y transacciones
  almacenamiento.py      carpetas por expediente, originales inmutables, copias de trabajo
  seguridad.py           saneado de nombres, protección de rutas, escritura exclusiva
  actividad.py           registro de actividad y logs técnicos sin contenido
  catalogo.py            operaciones, perfiles, tipos de documento y campos
  expedientes.py         expedientes y titulares
  ingesta.py             correos y archivos: asociación, procedencia, incidencias
  carpeta_entrada.py     vigilancia de 00 - ENTRADA
  incidencias.py         bandeja de incidencias
  documentos/            formato, texto PDF, OCR local y procesamiento
  extraccion/            interfaz de proveedor de IA, proveedor Anthropic, clave cifrada y normalización de valores
  datos.py               datos con estado y trazabilidad
  checklist/             requisitos configurables, vigencias y coherencia
  finanzas.py            cálculos deterministas (Decimal)
  situacion.py           reúne datos + cálculos para pantalla e informe
  informes/              contenido, DOCX/PDF y aprobación
  gmail/                 OAuth, token DPAPI, cliente y sincronización periódica
  web/                   interfaz (FastAPI + plantillas HTML)
scripts/                 documentos ficticios y arranque automático
tests/                   pruebas automáticas
ejemplos/                documentos y correos ficticios generados
```

## Limitaciones conocidas

- La IA solo recibe texto: un escaneado necesita OCR local (Tesseract) para poder analizarse. Sin texto, el documento queda pendiente de análisis.
- Documentos muy largos: solo se analizan las páginas que caben en `max_caracteres_texto`; se avisa de las que no se enviaron.
- La IA puede omitir datos; si una cita no se puede verificar, el dato se descarta. Revisa siempre los datos y añade a mano lo que falte.
- La extracción con IA no se ha probado todavía contra la API real en este equipo (no había clave); las pruebas automáticas usan un cliente simulado. Haz la prueba de la pantalla *IA* y el recorrido de la sección anterior antes de confiar en ella.
- Los ingresos de autónomos no se calculan automáticamente a partir de los modelos 130/303: introdúcelos como dato (*Ingresos netos mensuales*, declarado o acreditado).
- El prorrateo de pagas extra solo se aplica si consta el dato «Pagas anuales».
- Los impuestos de compra son un porcentaje orientativo configurable (dependen de la comunidad autónoma y del tipo de vivienda).
- Formatos admitidos: PDF, JPG, PNG y TIFF. El resto se guarda y genera una incidencia.
