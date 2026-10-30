@echo off
cd /d "%~dp0"
set CHANNEL=youth
".\venv\Scripts\python.exe" run_daily.py auto >> "logs\youth.log" 2>&1
