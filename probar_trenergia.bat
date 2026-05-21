@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "PAUSE_IF_NEEDED=if not defined TRENERGIA_NO_PAUSE pause"

echo ============================================================
echo TrEnergIA - prueba local mensual
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python no esta disponible en PATH.
    echo Instala Python 3.11+ o abre esta carpeta desde un terminal con Python.
    %PAUSE_IF_NEEDED%
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: no se pudo crear el entorno virtual.
        %PAUSE_IF_NEEDED%
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"
set "PYTHONUTF8=1"

echo Instalando dependencias...
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo ERROR: fallo la instalacion de dependencias.
    %PAUSE_IF_NEEDED%
    exit /b 1
)

echo.
echo Generando dashboard mensual de prueba ^(junio 2026, energia mock^)...
python dashboard.py --mes 2026-06 --mock-energia
if errorlevel 1 (
    echo ERROR: fallo la generacion del dashboard.
    %PAUSE_IF_NEEDED%
    exit /b 1
)

set "DASHBOARD=%CD%\salidas\mes_2026-06\dashboard.html"
echo.
echo Dashboard generado:
echo %DASHBOARD%

if exist "%DASHBOARD%" (
    start "" "%DASHBOARD%"
)

echo.
echo Prueba completada.
%PAUSE_IF_NEEDED%