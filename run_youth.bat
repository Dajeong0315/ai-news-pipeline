@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File ".\run-task.ps1" -Channel youth -Mode cards
exit /b %errorlevel%
