@echo off
cd /d "%~dp0"
".\venv\Scripts\python.exe" run_daily.py collect >> "logs\collect.log" 2>&1
