@echo off
rem Arranca la aplicación local y abre el navegador en http://127.0.0.1:8765/
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Falta el entorno. Ejecuta primero: powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m hipotecas %*
pause
