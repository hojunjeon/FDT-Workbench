@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
set PYTHONDONTWRITEBYTECODE=1
if exist ".venv\Scripts\python.exe" goto run
where py >nul 2>nul
if errorlevel 1 goto python_fallback
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto version_error
py -3 -m venv .venv
if errorlevel 1 goto setup_error
goto run
:python_fallback
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto version_error
python -m venv .venv
if errorlevel 1 goto setup_error
:run
".venv\Scripts\python.exe" launcher.py %*
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="0" exit /b 0
echo.
echo [ERROR] Workbench did not start. Read the error message above.
pause
exit /b %RESULT%
:version_error
echo [ERROR] Install Python 3.11 or newer, 64-bit. Enable Add Python to PATH.
pause
exit /b 1
:setup_error
echo [ERROR] Could not create .venv. Check Python, permissions, and free disk space.
pause
exit /b 1
