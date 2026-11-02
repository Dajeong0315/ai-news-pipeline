@echo off
cd /d "%~dp0"
rem every 10 min 07:30-22:00: regenerate requests + publish approved cards only (economy approval flow, 2026-11-02)
".\venv\Scripts\python.exe" publish_instagram.py --approved-only >> "logs\publish.log" 2>&1
