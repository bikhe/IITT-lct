@echo off
rem Windows: run.bat [port]  (double-click also works)
setlocal
rem Windows PowerShell 5.1 builds its own module path; one inherited from PowerShell 7 can break it
set "PSModulePath="
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 pause
