@echo off
rem PANAH - double-click to start the server and open the app in the browser.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    py -3 app\main.py %*
    goto done
)
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    python app\main.py %*
    goto done
)

echo.
echo   Python 3.10 or newer was not found on this computer.
echo   Download it from https://www.python.org/downloads/
echo   In the installer tick "Add python.exe to PATH", then run this file again.
echo.

:done
pause
endlocal
