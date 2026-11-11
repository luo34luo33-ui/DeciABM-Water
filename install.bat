@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Installation complete. Double-click start_backend.bat.
pause
exit /b 0
:failed
echo Installation failed. Check Python 3.12+ and your network connection.
pause
exit /b 1

