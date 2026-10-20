@echo off
title Folio
cd /d "%~dp0"
py -3.12 pc_launcher.py 2>nul || py pc_launcher.py 2>nul || python pc_launcher.py
pause
