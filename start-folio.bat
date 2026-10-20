@echo off
title Folio
cd /d "%~dp0"

rem --- find Python, and install it automatically if it's missing ---
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY ( python -c "import sys" >nul 2>&1 && set "PY=python" )
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"

if not defined PY (
  echo.
  echo   Installing Python ^(one time, about a minute^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$u='https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'; $o=Join-Path $env:TEMP 'python-folio.exe'; Invoke-WebRequest -Uri $u -OutFile $o; Start-Process -FilePath $o -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1' -Wait"
  if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
)

if not defined PY (
  echo.
  echo   Couldn't set up Python automatically.
  echo   Install Python 3.12 from https://python.org ^(tick "Add python.exe to PATH"^), then run this again.
  echo.
  pause
  exit /b
)

%PY% pc_launcher.py
pause
