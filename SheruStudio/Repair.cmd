@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0repair.ps1"
if errorlevel 1 echo Repair stopped. Read the error above.
pause
