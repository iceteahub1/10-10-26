@echo off
setlocal
cd /d "%~dp0"
if not exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
 echo Windows PowerShell is missing. Ask your group lead to repair Windows PowerShell.
 pause
 exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows_bootstrap.ps1" -Mode Launch %*
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Read the message above, then retry. Your restaurant data has not been deleted.
pause
exit /b %RESULT%
