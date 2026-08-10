@echo off
rem ASCII only - same reason as setup.bat.
rem
rem Makes dist\Auto-Concur\Auto-Concur.exe. Run this on a PC where pip works,
rem then copy that folder to the locked-down PC. Some tools (DuoNX) can only
rem register an executable - no arguments, no working directory - so a .bat or
rem a .py file cannot be registered but this exe can.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe goto notyet

echo Installing the build tool...
rem --upgrade matters: PyInstaller has to know the Python it is packing. An
rem older one packs a newer Python into an exe that dies at startup with
rem "Failed to import encodings module" (seen with Python 3.14.5).
venv\Scripts\python.exe -m pip install --upgrade pyinstaller --quiet
if errorlevel 1 goto pipfail

rem Print both versions. When the exe will not start, these two lines are the
rem first thing worth looking at.
echo.
echo Building with:
venv\Scripts\python.exe --version
venv\Scripts\python.exe -m PyInstaller --version

rem Wipe dist first. An earlier --onefile build leaves a FILE named
rem dist\Auto-Concur.exe, and --onedir now needs a FOLDER named
rem dist\Auto-Concur. PyInstaller cannot make one over the other, and --clean
rem only clears its own cache, not dist. Leaving it there breaks the build.
if exist dist rmdir /s /q dist

echo.
echo Building. This takes a few minutes...
rem --collect-submodules src: the window loads each step with importlib at the
rem   moment its button is pressed. PyInstaller reads the source to decide what
rem   to pack and cannot see a name that is only built at runtime, so without
rem   this only src.gui gets packed and the first button says
rem   "No module named 'src.download_slips'".
rem --collect-all playwright: playwright ships a Node driver next to the Python
rem   package. Without it the exe starts and then fails to launch any browser.
rem --onedir, not --onefile: onefile unpacks a few hundred files into %TEMP% on
rem   EVERY launch, and a real-time scanner locks them while it checks. That
rem   showed up as PermissionError on files that do not exist in the source at
rem   all. onedir writes those files once, at build time, so the scanner sees
rem   them once. The cost is that you copy a folder instead of a single file.
rem Console stays on: it is where a crash before the window opens shows up.
venv\Scripts\python.exe -m PyInstaller ^
  --onedir ^
  --name Auto-Concur ^
  --collect-submodules src ^
  --collect-all playwright ^
  --clean ^
  --noconfirm ^
  launcher.py
if errorlevel 1 goto fail

echo.
echo ================================================================
echo   Done: dist\Auto-Concur\Auto-Concur.exe
echo.
echo   Copy the WHOLE dist\Auto-Concur folder, not just the exe.
echo   The exe keeps settings.json, downloads and browser-profile
echo   next to itself, inside that folder.
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
