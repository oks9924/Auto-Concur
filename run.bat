@echo off
rem ASCII only - same reason as setup.bat.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe goto notyet

rem Refuse stale virtual environments. The verified runtime is Python 3.12 + Playwright 1.63.0.
venv\Scripts\python.exe -c "import sys,importlib.metadata as m; raise SystemExit(0 if sys.version_info[:2] == (3,12) and m.version('playwright') == '1.63.0' else 1)" >nul 2>&1
if errorlevel 1 goto wrongenv

rem Keep everything the program prints in a file. When the window closes on an
rem error there is nothing left to read otherwise, and a company network breaks
rem things in ways only the traceback explains.
venv\Scripts\python.exe -m src.gui > run-log.txt 2>&1
if errorlevel 1 goto crashed
exit /b 0

:crashed
echo.
echo ================================================================
echo   The program stopped with an error.
echo   The same text is saved in run-log.txt next to this file.
echo ================================================================
echo.
type run-log.txt
echo.
pause
exit /b 1

:wrongenv
echo.
echo ================================================================
echo   Runtime mismatch.
echo   Auto-Concur now requires Python 3.12 + Playwright 1.63.0.
echo   Close Auto-Concur and run setup.bat once to rebuild venv.
echo ================================================================
echo.
venv\Scripts\python.exe --version 2>nul
venv\Scripts\python.exe -c "import importlib.metadata as m; print('Playwright', m.version('playwright'))" 2>nul
pause
exit /b 1

:notyet
echo Not installed yet. Double-click setup.bat first.
pause
exit /b 1
