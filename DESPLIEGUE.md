# Despliegue en el servidor (Ubuntu + Docker)

```
Móvil / PC ──► Drive: GESTOR HIPOTECARIO/00 - ENTRADA ──(cada 2 min)──► Servidor: procesa
Gmail (etiqueta + remitentes de tus expedientes) ─────────────────────► Servidor: procesa
Servidor: 02 - EXPEDIENTES, 03 - INCIDENCIAS, copias de la BD ──(cada 2 min)──► Drive ──► tu PC
Móvil / PC ──(Tailscale, red privada)──► https://<servidor>.ts.net  (revisar, validar, aprobar)
```

- El **servidor** trabaja las 24 horas: recoge el correo y la carpeta de entrada, ejecuta el OCR y la IA, y genera los informes.
- **Google Drive** es tu ventana a los archivos. En `00 - ENTRADA` dejas documentos; en `02 - EXPEDIENTES` ves el resultado, ordenado y renombrado.
- La **aplicación** no queda abierta a Internet: solo se entra desde tus dispositivos conectados a Tailscale.

> En Drive, `02 - EXPEDIENTES` y `03 - INCIDENCIAS` son **de solo consulta**. Las correcciones se hacen en la aplicación. Si cambias algo a mano en esas carpetas de Drive, se sustituye en la siguiente sincronización. Lo sustituido no se pierde: se guarda en `GESTOR HIPOTECARIO/.historial/<fecha>`.

Todo lo propio del servidor queda en la carpeta `servidor/`, excluida de git:

| Ruta | Contenido |
|---|---|
| `servidor/config.toml` | Configuración |
| `servidor/.env` | Variables: clave de IA opcional y nombres de Drive |
| `servidor/datos/` | Base de datos, expedientes, secretos (token de Gmail, clave de IA) y copias diarias de la BD |
| `servidor/rclone/` | Conexión con Google Drive |

---

## Qué necesitas

- El servidor Ubuntu con Docker. Comprueba que `docker compose version` funciona.
- Una cuenta de **Tailscale** (gratis): <https://tailscale.com>.
- Una cuenta de Google para **Gmail** y otra (o la misma) para **Drive**. Con Google Workspace, la autorización no caduca.
- Una clave de la API de **Anthropic** (<https://console.anthropic.com/>). Se paga por uso y es distinta de la suscripción a Claude.
- En tu PC: Tailscale, Google Drive para escritorio y rclone (este último solo hace falta una vez, para autorizar Drive).

## 1. Instalar la aplicación en el servidor

Sube primero los cambios a GitHub desde tu PC. Después, en el servidor:

```bash
ssh TU_USUARIO@IP_DEL_SERVIDOR
sudo git clone https://github.com/atoroj/LT-Automator.git /opt/gestor
sudo chown -R "$USER" /opt/gestor
cd /opt/gestor
bash despliegue/instalar_servidor.sh
```

Si el repositorio es privado, `git clone` pedirá usuario y un *token* de GitHub en lugar de la contraseña. Lo creas en GitHub → Settings → Developer settings → Personal access tokens.

El script crea `servidor/`, copia la configuración de ejemplo, construye la imagen (con OCR en español) y arranca dos contenedores:
- `gestor-hipotecario`: la aplicación.
- `gestor-drive`: la sincronización con Drive. Hasta que configures Drive (paso 4), solo espera.

Comprobación: `docker compose ps` muestra los dos contenedores en marcha, y `curl -s http://127.0.0.1:8765/ | head -3` devuelve HTML.

## 2. Acceso privado con Tailscale

En el servidor:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                 # abre el enlace que muestra e inicia sesión
sudo tailscale serve --bg 8765    # publica la aplicación solo dentro de tu red Tailscale, con HTTPS
sudo tailscale serve status       # muestra la dirección: https://NOMBRE.tailXXXX.ts.net
```

Si `tailscale serve` te pide activar HTTPS, hazlo en <https://login.tailscale.com/admin/dns> (MagicDNS y *HTTPS Certificates*).

Pon ese nombre en `servidor/config.toml`, sin `https://` y sin barra final, y reinicia:

```toml
[servidor]
hosts_permitidos = ["NOMBRE.tailXXXX.ts.net"]
```
```bash
docker compose restart gestor
```

En el **móvil y en el PC**, instala Tailscale e inicia sesión con la misma cuenta. Después abre `https://NOMBRE.tailXXXX.ts.net` y guárdala en favoritos.

## 3. Clave de IA

En la aplicación: pantalla **IA** → pega la clave en *Guardar clave* → **Enviar prueba**. Esa prueba solo manda un texto inventado. La clave queda en `servidor/datos/secretos`, con permisos de solo propietario.

Alternativa: ponla en `servidor/.env` (`ANTHROPIC_API_KEY=...`) y ejecuta `docker compose up -d`.

## 4. Conectar Google Drive

1. En tu **PC** (PowerShell), instala rclone: `winget install Rclone.Rclone`, y abre una terminal nueva.
2. En el **servidor**:
   ```bash
   cd /opt/gestor
   docker compose run --rm --entrypoint rclone drive config
   ```
   Responde a las preguntas principales (el orden puede variar un poco según la versión):
   - `n`: nuevo remoto. Nombre: **`drive`**. Almacenamiento: **Google Drive** (`drive`).
   - `client_id` y `client_secret`: en blanco.
   - Alcance (*scope*): **`drive`** (acceso completo). Es necesario para ver los archivos que subas desde el móvil.
   - `root_folder_id` y *service account*: en blanco. *Advanced config*: `n`.
   - *Use web browser to automatically authenticate?*: **`n`**. Te mostrará un comando `rclone authorize "drive" "…"`.
3. Copia ese comando y ejecútalo en la terminal de tu **PC**. Se abre el navegador: elige la **cuenta de Google cuyo Drive quieres usar** y acepta. La terminal del PC muestra un código: cópialo y pégalo en el servidor.
4. *Shared Drive*: `n`. Confirma con `y` y sal con `q`.
5. Reinicia la sincronización y mira el registro:
   ```bash
   docker compose restart drive
   docker compose logs -f drive     # debe decir «Sincronización con drive:GESTOR HIPOTECARIO activa»
   ```
6. En tu Drive aparece `GESTOR HIPOTECARIO/00 - ENTRADA`, con una subcarpeta por expediente abierto.
7. En el PC, con Google Drive para escritorio, verás la carpeta `GESTOR HIPOTECARIO` como una carpeta normal.

## 5. Conectar Gmail

1. En <https://console.cloud.google.com/>: crea un proyecto y habilita la **Gmail API**.
2. En la pantalla de consentimiento, elige el tipo **Interno** si usas Workspace (si es @gmail.com, **Externo** y añádete como usuaria de prueba). Añade el permiso `https://www.googleapis.com/auth/gmail.readonly`.
3. Ve a *Credenciales → Crear credenciales → ID de cliente de OAuth → **Aplicación de escritorio***. Descarga el JSON.
4. En la aplicación, pantalla **Entrada → Gmail → Credenciales OAuth**: sube el JSON.
5. Pulsa **Conectar Gmail** → **Abrir Google y aceptar**. Elige la cuenta de correo y acepta el acceso de solo lectura.
6. El navegador intentará abrir `http://127.0.0.1:8765/?state=…&code=…` y dirá que no puede acceder. **Es normal.** Copia esa dirección completa de la barra del navegador, pégala en la casilla de la pantalla **Entrada** y pulsa **Completar conexión**.
7. En Gmail, crea la etiqueta `Hipotecas/Procesar` para los correos que quieras forzar. Los correos con adjuntos de los contactos de tus expedientes se recogen solos aunque no los etiquetes.

## 6. Comprobar que todo funciona (con documentos ficticios)

1. Crea un expediente de prueba en la aplicación, con el correo de contacto de una cuenta tuya de pruebas.
2. Desde el móvil, sube `ejemplos/documentos_ficticios/contrato_arras.pdf` a `Drive/GESTOR HIPOTECARIO/00 - ENTRADA/EXP-… - Nombre/`.
3. En 2–3 minutos el archivo desaparece de la entrada de Drive. En la aplicación aparece en el expediente, clasificado por la IA y con sus datos. Poco después también está en `Drive/GESTOR HIPOTECARIO/02 - EXPEDIENTES/…/05_Vivienda/`.
4. Envía desde la cuenta de pruebas un correo con un PDF ficticio a tu Gmail. En unos 5 minutos aparece en el expediente.
5. Sube un PDF suelto (sin referencia) a `00 - ENTRADA`: debe ir a *Incidencias*.

---

## Cambiar de PC

En el PC no hay nada de la aplicación. En el nuevo solo tienes que instalar **Tailscale** y **Google Drive para escritorio** e iniciar sesión. Nada más.

## Cambiar de cuenta de Google

Las cuentas de Gmail, Drive y la IA son independientes; puedes cambiar una sin tocar las demás.

- **Gmail:** pantalla **Entrada** → *Reconectar / cambiar de cuenta* y elige la otra cuenta. Si la nueva cuenta usa otro proyecto de Google Cloud, sube antes su `credentials.json`.
- **Drive:**
  ```bash
  docker compose run --rm --entrypoint rclone drive config reconnect drive:
  docker compose restart drive
  ```
  Autoriza con la nueva cuenta (mismo proceso del paso 4). En el siguiente ciclo, el Drive nuevo recibe la copia completa de los expedientes. La entrada pasa a ser la de la cuenta nueva.
- **IA:** pantalla **IA** → guarda la nueva clave.

## Cambiar de servidor

**Opción rápida** (con acceso al servidor antiguo): para los contenedores y copia la carpeta `servidor/` completa al nuevo. Incluye datos, secretos y la conexión con Drive. Después, en el nuevo:

```bash
docker compose down                                   # en el servidor antiguo
scp -r /opt/gestor/servidor NUEVO:/tmp/servidor       # copiar
# en el servidor nuevo: pasos 1 y 2; luego
sudo cp -a /tmp/servidor/. /opt/gestor/servidor/ && sudo chown -R 1000:1000 /opt/gestor/servidor/datos /opt/gestor/servidor/rclone
docker compose up -d
```

**Desde Drive** (si el servidor antiguo ya no existe):
1. Haz los pasos 1, 2 y 4 en el servidor nuevo.
2. Restaura los expedientes y la última copia de la base de datos:
   ```bash
   docker compose stop gestor
   docker compose run --rm --entrypoint /bin/sh drive /scripts/restaurar_desde_drive.sh
   docker compose up -d
   ```
3. Vuelve a guardar la clave de IA y a conectar Gmail (los secretos no se copian a Drive).

## Actualizar la aplicación

```bash
cd /opt/gestor && git pull && docker compose up -d --build
```

Tus datos y tu configuración están en `servidor/` y no se tocan.

## Problemas frecuentes

| Síntoma | Solución |
|---|---|
| «Host no permitido» o «origen no permitido» | El nombre de `hosts_permitidos` no coincide con el de `tailscale serve status`. Corrígelo y ejecuta `docker compose restart gestor`. |
| La página no carga desde el móvil | El móvil no tiene Tailscale activo, o falta `sudo tailscale serve --bg 8765`. |
| `docker compose logs drive` dice «Drive sin configurar» | Repite el paso 4. El remoto debe llamarse `drive` (o lo que pongas en `DRIVE_REMOTO`). |
| Errores de permisos en `servidor/datos` | `sudo chown -R 1000:1000 servidor/datos servidor/rclone` |
| Gmail: «la autorización ha caducado» | Pantalla **Entrada** → *Reconectar*. Con Workspace y tipo *Interno* no debería pasar. |
| Ver qué está pasando | `docker compose logs -f gestor` y `docker compose logs -f drive` |
| Espacio en disco | `du -sh servidor/datos`. Cada expediente ocupa pocos MB. |
