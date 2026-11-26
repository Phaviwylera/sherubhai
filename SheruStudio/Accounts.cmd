@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1" -Action accounts
if errorlevel 1 echo The command stopped. Read the error above.
pause
