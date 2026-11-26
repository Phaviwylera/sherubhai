@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fresh-install.ps1"
if errorlevel 1 echo Setup stopped. Read the error above. Completed downloads are retained.
pause
