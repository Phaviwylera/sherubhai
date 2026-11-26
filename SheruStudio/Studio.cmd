@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" -Action studio
if errorlevel 1 echo Studio stopped. Read the error above.
pause
