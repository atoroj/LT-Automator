#!/bin/sh
# Restaura expedientes, incidencias y la última copia de la base de datos desde Google Drive.
# Para montar la aplicación en un servidor nuevo (ver DESPLIEGUE.md, «Cambiar de servidor»).
# Se ejecuta con la aplicación PARADA:
#   docker compose stop gestor
#   docker compose run --rm --entrypoint /bin/sh drive /scripts/restaurar_desde_drive.sh
set -eu

REMOTO="${DRIVE_REMOTO:-drive}"
BASE="${DRIVE_CARPETA:-GESTOR HIPOTECARIO}"
RAIZ_LOCAL="/datos/${CARPETA_RAIZ:-GESTOR HIPOTECARIO}"
EXPEDIENTES="${CARPETA_EXPEDIENTES:-02 - EXPEDIENTES}"
INCIDENCIAS="${CARPETA_INCIDENCIAS:-03 - INCIDENCIAS}"
DESTINO="$REMOTO:$BASE"

if [ -f /datos/hipotecas.db ]; then
  echo "Ya existe /datos/hipotecas.db. Para no sobrescribir nada, la restauración se detiene."
  echo "Si de verdad quieres restaurar, mueve antes servidor/datos a otra carpeta."
  exit 1
fi

echo "Descargando expedientes e incidencias..."
rclone copy "$DESTINO/$EXPEDIENTES" "$RAIZ_LOCAL/$EXPEDIENTES" --log-level NOTICE
rclone copy "$DESTINO/$INCIDENCIAS" "$RAIZ_LOCAL/$INCIDENCIAS" --log-level NOTICE

ULTIMA="$(rclone lsf "$DESTINO/.copias_bd" --include 'hipotecas_*.db' | sort | tail -n 1)"
if [ -z "$ULTIMA" ]; then
  echo "No hay copias de la base de datos en Drive."
  exit 1
fi
echo "Restaurando base de datos: $ULTIMA"
rclone copyto "$DESTINO/.copias_bd/$ULTIMA" /datos/hipotecas.db
echo "Hecho. Arranca la aplicación con: docker compose up -d"
echo "Después vuelve a guardar la clave de IA y a conectar Gmail (los secretos no se copian a Drive)."
