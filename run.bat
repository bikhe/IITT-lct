@echo off
rem Windows: run.bat [port]  (double-click also works)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 pause
