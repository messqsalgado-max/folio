@echo off
title Folio Update
cd /d "%~dp0"

set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY ( python -c "import sys" >nul 2>&1 && set "PY=python" )
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"

if not defined PY (
  echo   Run start-folio.bat once first ^(it installs Python^), then use this to update.
  pause
  exit /b
)

%PY% pc_launcher.py --update
pause
