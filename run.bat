@echo off
rem ASCII only - same reason as setup.bat.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe goto notyet
rem python.exe, not pythonw.exe: a console window is ugly but errors stay visible.
venv\Scripts\python.exe -m src.gui
exit /b 0

:notyet
echo Not installed yet. Double-click setup.bat first.
pause
exit /b 1
