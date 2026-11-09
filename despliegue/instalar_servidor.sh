#!/usr/bin/env bash
# Prepara y arranca el gestor en el servidor (Ubuntu con Docker).
# Uso, desde la carpeta del proyecto:   bash despliegue/instalar_servidor.sh
# Se puede repetir sin riesgo: no sobrescribe la configuración ni los datos existentes.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p servidor/datos servidor/rclone
if [ ! -f servidor/config.toml ]; then
  cp despliegue/config.servidor.example.toml servidor/config.toml
  echo "Creado servidor/config.toml (revisa hosts_permitidos)."
fi
if [ ! -f servidor/.env ]; then
  cp despliegue/env.example servidor/.env
  echo "Creado servidor/.env."
fi
chmod 600 servidor/.env
# Los contenedores trabajan con el usuario 1000 (sin privilegios de administrador).
sudo chown -R 1000:1000 servidor/datos servidor/rclone

docker compose up -d --build
docker compose ps
echo
echo "Aplicación en marcha en el servidor (127.0.0.1:8765)."
echo "Siguiente paso: Tailscale, Drive, clave de IA y Gmail (DESPLIEGUE.md)."
