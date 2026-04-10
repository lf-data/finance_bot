@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON=%SCRIPT_DIR%.conda\python.exe"

if not exist "%PYTHON%" (
    echo [ERRORE] Ambiente conda non trovato: %PYTHON%
    echo Esegui prima: conda create -p .conda python=3.12
    pause
    exit /b 1
)

"%PYTHON%" "%SCRIPT_DIR%screener.py" %*
