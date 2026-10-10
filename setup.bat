@echo off
setlocal
cd /d "%~dp0"
py -3.12 "%~dp0scripts\setup_environment.py" --probe >nul 2>&1
if not errorlevel 1 goto py312
py -3.11 "%~dp0scripts\setup_environment.py" --probe >nul 2>&1
if not errorlevel 1 goto py311
python "%~dp0scripts\setup_environment.py" --probe >nul 2>&1
if not errorlevel 1 goto python
 echo ERROR: Install 64-bit Python 3.11 or 3.12 from https://www.python.org/downloads/windows/
 echo Enable the Python launcher during installation.
 pause
 exit /b 1
:py312
py -3.12 "%~dp0scripts\setup_environment.py" %*
goto done
:py311
py -3.11 "%~dp0scripts\setup_environment.py" %*
goto done
:python
python "%~dp0scripts\setup_environment.py" %*
:done
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Setup did not complete. Read the error above.
pause
exit /b %RESULT%
