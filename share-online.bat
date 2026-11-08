@echo off
rem PANAH - share the app with phones over a secure https link (Cloudflare quick tunnel, free, no account).
rem Needs cloudflared.exe next to this file: see HOSTING.md. The link works while this window is open.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem --- find cloudflared: next to this file (any of the release names) or installed in PATH
set "TUNNEL="
if exist "cloudflared.exe" set "TUNNEL=%CD%\cloudflared.exe"
if not defined TUNNEL if exist "cloudflared-windows-amd64.exe" set "TUNNEL=%CD%\cloudflared-windows-amd64.exe"
if not defined TUNNEL if exist "cloudflared-windows-386.exe" set "TUNNEL=%CD%\cloudflared-windows-386.exe"
if not defined TUNNEL for /f "delims=" %%i in ('where cloudflared.exe 2^>nul') do if not defined TUNNEL set "TUNNEL=%%i"
if defined TUNNEL goto find_python

echo.
echo   cloudflared.exe was not found next to share-online.bat.
echo   It makes the secure https link for phones. Get it once (free, no account needed):
echo.
echo     1. Download this file (official Cloudflare release on GitHub):
echo        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
echo        All versions: https://github.com/cloudflare/cloudflared/releases/latest
echo     2. Rename it to cloudflared.exe and put it in this folder:
echo        "%CD%"
echo     3. Double-click share-online.bat again.
echo.
echo   Without it, run.bat still works on this computer and in the same Wi-Fi (but no GPS on phones).
echo.
goto done

rem --- find Python 3.10+ (the same way as run.bat)
:find_python
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if not defined PY if not errorlevel 1 set "PY=python"
if not defined PY goto no_python

rem --- 127.0.0.1: only this computer and the tunnel reach the server (no firewall question)
%PY% app\main.py --host 127.0.0.1 --tunnel "%TUNNEL%" %*
goto done

:no_python
echo.
echo   Python 3.10 or newer was not found on this computer.
echo   Download it from https://www.python.org/downloads/
echo   In the installer tick "Add python.exe to PATH", then run this file again.
echo.

:done
pause
endlocal
