@echo off
rem ASCII only - same reason as setup.bat.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe goto notyet

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

:notyet
echo Not installed yet. Double-click setup.bat first.
pause
exit /b 1
