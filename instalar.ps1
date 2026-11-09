# Instalación en Windows. Ejecutar desde la carpeta del proyecto:
#   powershell -ExecutionPolicy Bypass -File .\instalar.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual de Python..."
    py -3 -m venv .venv
    if (-not $?) { python -m venv .venv }
}
Write-Host "Instalando dependencias..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path "config.toml")) {
    Copy-Item "config.example.toml" "config.toml"
    Write-Host "Creado config.toml a partir de config.example.toml (revísalo)."
}

Write-Host "Generando documentos y correos ficticios de prueba..."
& .\.venv\Scripts\python.exe scripts\generar_documentos_ficticios.py

Write-Host "Ejecutando pruebas automáticas..."
& .\.venv\Scripts\python.exe -m pytest -q

Write-Host ""
Write-Host "Instalación completada. Arranca la aplicación con iniciar.bat"
