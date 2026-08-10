@echo off
rem ASCII only - same reason as setup.bat.
rem
rem Makes dist\Auto-Concur.exe. Run this on a PC where pip works, then copy the
rem exe to the locked-down PC. Some tools (DuoNX) can only register an
rem executable - no arguments, no working directory - so a .bat or a .py file
rem cannot be registered but this exe can.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe goto notyet

echo Installing the build tool...
venv\Scripts\python.exe -m pip install pyinstaller --quiet
if errorlevel 1 goto pipfail

echo.
echo Building. This takes a few minutes...
rem --collect-submodules src: the window loads each step with importlib at the
rem   moment its button is pressed. PyInstaller reads the source to decide what
rem   to pack and cannot see a name that is only built at runtime, so without
rem   this only src.gui gets packed and the first button says
rem   "No module named 'src.download_slips'".
rem --collect-all playwright: playwright ships a Node driver next to the Python
rem   package. Without it the exe starts and then fails to launch any browser.
rem --onefile: one file to register and copy. Nothing else to install.
rem Console stays on: it is where a crash before the window opens shows up.
venv\Scripts\python.exe -m PyInstaller ^
  --onefile ^
  --name Auto-Concur ^
  --collect-submodules src ^
  --collect-all playwright ^
  --clean ^
  --noconfirm ^
  launcher.py
if errorlevel 1 goto fail

echo.
echo ================================================================
echo   Done: dist\Auto-Concur.exe
echo.
echo   Copy the exe into the folder you want to work in. It keeps
echo   settings.json, downloads and browser-profile NEXT TO ITSELF,
echo   so put it in its own folder.
echo ================================================================
pause
exit /b 0

:pipfail
echo.
echo Could not install pyinstaller. A company proxy blocking pip is the
echo usual cause. Build on a PC with normal internet and copy the exe.
goto fail

:notyet
echo Not installed yet. Double-click setup.bat first.
goto fail

:fail
echo.
pause
exit /b 1
