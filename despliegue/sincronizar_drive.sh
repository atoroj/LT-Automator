#!/bin/sh
# Sincronización con Google Drive (se ejecuta dentro del contenedor rclone, en bucle).
#
#   Drive/GESTOR HIPOTECARIO/00 - ENTRADA      ->  servidor   (se mueve: lo procesa la aplicación)
#   servidor  02 - EXPEDIENTES / 03 - INCIDENCIAS  ->  Drive  (espejo de solo consulta)
#   servidor  copias de la base de datos            ->  Drive  (.copias_bd, para restaurar en otro servidor)
#
# Lo que el espejo sustituye o elimina en Drive no se pierde: se guarda en Drive/.historial/<fecha>.
set -u

REMOTO="${DRIVE_REMOTO:-drive}"
BASE="${DRIVE_CARPETA:-GESTOR HIPOTECARIO}"
RAIZ_LOCAL="/datos/${CARPETA_RAIZ:-GESTOR HIPOTECARIO}"
ENTRADA="${CARPETA_ENTRADA:-00 - ENTRADA}"
EXPEDIENTES="${CARPETA_EXPEDIENTES:-02 - EXPEDIENTES}"
INCIDENCIAS="${CARPETA_INCIDENCIAS:-03 - INCIDENCIAS}"
INTERVALO="${INTERVALO_DRIVE_SEGUNDOS:-120}"
DESTINO="$REMOTO:$BASE"

esperar_configuracion() {
  while ! rclone listremotes 2>/dev/null | grep -qx "$REMOTO:"; do
    echo "$(date '+%F %T') Drive sin configurar: falta el remoto '$REMOTO' en rclone.conf (ver DESPLIEGUE.md). Reintento en 5 min."
    sleep 300
  done
}

esperar_configuracion
rclone mkdir "$DESTINO/$ENTRADA"
echo "$(date '+%F %T') Sincronización con $DESTINO activa (cada ${INTERVALO}s)"

while true; do
  # 1) Entrada: lo que dejes en Drive pasa al servidor y se retira de Drive tras copiarlo y verificarlo.
  rclone move "$DESTINO/$ENTRADA" "$RAIZ_LOCAL/$ENTRADA" \
    --min-age 30s --exclude "desktop.ini" --exclude ".*" --log-level NOTICE

  # 2) Las subcarpetas de cada expediente (que crea la aplicación) aparecen también en Drive.
  for d in "$RAIZ_LOCAL/$ENTRADA"/*/; do
    [ -d "$d" ] && rclone mkdir "$DESTINO/$ENTRADA/$(basename "$d")"
  done

  # 3) Expedientes e incidencias: espejo servidor -> Drive.
  HOY="$(date +%Y-%m-%d)"
  for c in "$EXPEDIENTES" "$INCIDENCIAS"; do
    if [ -d "$RAIZ_LOCAL/$c" ]; then
      rclone sync "$RAIZ_LOCAL/$c" "$DESTINO/$c" --backup-dir "$DESTINO/.historial/$HOY/$c" --log-level NOTICE
    fi
  done

  # 4) Copias diarias de la base de datos.
  [ -d /datos/copias ] && rclone copy /datos/copias "$DESTINO/.copias_bd" --log-level NOTICE

  sleep "$INTERVALO"
done
