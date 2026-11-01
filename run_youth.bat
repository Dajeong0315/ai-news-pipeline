@echo off
cd /d "%~dp0"
set CHANNEL=youth
rem 12:00/19:00 cards only (select 1 + Telegram approval request). Publishing: run_youth_publish.bat
".\venv\Scripts\python.exe" run_daily.py cards >> "logs\youth.log" 2>&1
