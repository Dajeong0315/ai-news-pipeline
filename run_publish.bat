@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ".\run-task.ps1" -Channel economy -Mode approved
exit /b %errorlevel%
