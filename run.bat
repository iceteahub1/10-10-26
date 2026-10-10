@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
 echo ERROR: Project environment is missing. Run setup.bat first.
 pause
 exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\launch_application.py" %*
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Startup failed. Read the error above and check logs.
pause
exit /b %RESULT%
