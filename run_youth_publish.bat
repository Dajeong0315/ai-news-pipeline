@echo off
cd /d "%~dp0"
set CHANNEL=youth
rem every 10 min 09:00-23:00: regenerate requests + publish approved cards only
".\venv\Scripts\python.exe" publish_instagram.py --approved-only >> "logs\youth_publish.log" 2>&1
