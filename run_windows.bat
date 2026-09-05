@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ============================================================
echo SEEFIX Facility Inspection Agent POC
echo ============================================================
echo.

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"
set "OLLAMA_EXE=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

if not exist "%VENV_PYTHON%" (
    echo [SETUP] Creating the Python virtual environment...
    where py >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python launcher ^(py.exe^) was not found.
        echo Install Python 3.11 or 3.12, then run this file again.
        pause
        exit /b 1
    )

    py -3.12 -m venv .venv 2>nul
    if not exist "%VENV_PYTHON%" py -3.11 -m venv .venv 2>nul
    if not exist "%VENV_PYTHON%" py -3.10 -m venv .venv 2>nul

    if not exist "%VENV_PYTHON%" (
        echo [ERROR] Unable to create .venv with Python 3.10, 3.11, or 3.12.
        pause
        exit /b 1
    )
)

echo [INFO] Python executable:
"%VENV_PYTHON%" -c "import sys; print(sys.executable)"
if errorlevel 1 goto :failure

echo.
echo [SETUP] Installing required Python packages...
"%VENV_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failure

"%VENV_PYTHON%" -m pip install -r requirements-local.txt
if errorlevel 1 goto :failure

echo.
echo [CHECK] Verifying FastAPI multipart support...
"%VENV_PYTHON%" -c "import fastapi, multipart, uvicorn; print('FastAPI dependencies are ready.')"
if errorlevel 1 (
    echo [REPAIR] Reinstalling python-multipart...
    "%VENV_PYTHON%" -m pip uninstall -y multipart >nul 2>nul
    "%VENV_PYTHON%" -m pip install --force-reinstall python-multipart
    if errorlevel 1 goto :failure
)

if exist "%OLLAMA_EXE%" (
    set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
) else (
    where ollama >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [ERROR] Ollama is not installed.
        echo Download it from: https://ollama.com/download/windows
        pause
        exit /b 1
    )
)

echo.
echo [CHECK] Ollama version:
ollama --version
if errorlevel 1 goto :failure

if not exist .env (
    if exist .env.example (
        copy /y .env.example .env >nul
        echo [INFO] Created .env from .env.example.
    ) else (
        echo [ERROR] The .env configuration file is missing.
        pause
        exit /b 1
    )
)

echo.
echo [START] Starting SEEFIX on http://127.0.0.1:8000
echo [INFO] Press CTRL+C to stop the server.
echo.

"%VENV_PYTHON%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%

:failure
echo.
echo [ERROR] Setup failed. Review the error shown above.
pause
exit /b 1
