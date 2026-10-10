@echo off
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_bootstrap.ps1" -Mode ConfigureAI
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%
