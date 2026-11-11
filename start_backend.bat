@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run install.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
.venv\Scripts\python.exe -c "import socket,sys; s=socket.socket(); sys.exit(1 if s.connect_ex(('127.0.0.1',8000)) == 0 else 0)"
if errorlevel 1 (
  echo Port 8000 is already in use. Close the previous demo backend window, then run this file again to load the latest code.
  pause
  exit /b 1
)
start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000'"
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
