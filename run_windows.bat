@echo off
setlocal
if not exist .venv\Scripts\python.exe (
  echo [SEEFIX] Missing .venv. Create it with: py -3.12 -m venv .venv
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
endlocal
