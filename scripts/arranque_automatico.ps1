# Activa o desactiva el arranque automático al iniciar sesión en Windows.
# Crea (o elimina) un acceso directo en la carpeta Inicio del usuario que lanza la
# aplicación en segundo plano, sin ventana y sin abrir el navegador.
#   powershell -ExecutionPolicy Bypass -File .\scripts\arranque_automatico.ps1 -Activar
#   powershell -ExecutionPolicy Bypass -File .\scripts\arranque_automatico.ps1 -Desactivar
param([switch]$Activar, [switch]$Desactivar)
$ErrorActionPreference = "Stop"

$raiz = Split-Path -Parent $PSScriptRoot
$inicio = [Environment]::GetFolderPath("Startup")
$acceso = Join-Path $inicio "Expedientes hipotecarios.lnk"

if ($Activar) {
    $pythonw = Join-Path $raiz ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pythonw)) { throw "No se encuentra $pythonw. Ejecuta antes instalar.ps1" }
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($acceso)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "-m hipotecas --sin-navegador"
    $lnk.WorkingDirectory = $raiz
    $lnk.WindowStyle = 7
    $lnk.Description = "Gestión local de expedientes hipotecarios (127.0.0.1)"
    $lnk.Save()
    Write-Host "Arranque automático activado: $acceso"
    Write-Host "Abre http://127.0.0.1:8765/ en el navegador (o ejecuta iniciar.bat, que solo abrirá el navegador si ya está en marcha)."
}
elseif ($Desactivar) {
    if (Test-Path $acceso) { Remove-Item $acceso; Write-Host "Arranque automático desactivado." }
    else { Write-Host "No estaba activado." }
}
else {
    Write-Host "Uso: arranque_automatico.ps1 -Activar | -Desactivar"
}
