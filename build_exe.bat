@echo off
rem ASCII only - same reason as setup.bat.
rem
rem Makes ONE file: dist\Auto-Concur.exe
rem
rem That single file is the whole program. Hand it to a colleague and they
rem double-click it. No Python, no pip, no setup.bat on their side.
rem
rem They do need Microsoft Edge, which every Windows PC already has. We drive
rem the Edge that is on the machine instead of shipping a browser - that keeps
rem this exe at tens of megabytes instead of hundreds.
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

rem Wipe dist first. A previous build may have left a FOLDER where we now want
rem a FILE (or the other way round), and PyInstaller cannot make one over the
rem other. --clean only clears its own cache, not dist.
if exist dist rmdir /s /q dist

echo.
echo Building. This takes a few minutes...
rem --onefile: one file to hand over. It unpacks itself into %TEMP% on each
rem   launch, so the first window takes a few seconds to appear. On a PC with
rem   an aggressive real-time scanner that unpacking can get locked - build
rem   with --onedir there instead and hand over the whole folder.
rem --collect-submodules src: the window loads each step with importlib at the
rem   moment its button is pressed. PyInstaller reads the source to decide what
rem   to pack and cannot see a name that is only built at runtime, so without
rem   this only src.gui gets packed and the first button says
rem   "No module named 'src.download_slips'".
rem --collect-all playwright: playwright ships a Node driver next to the Python
rem   package. Without it the exe starts and then fails to launch any browser.
rem --noupx: UPX-compressed executables get flagged by antivirus far more often.
rem   Size is not worth that.
rem Console stays on: it is where a crash before the window opens shows up, and
rem   this program has crashed that way more than once.
venv\Scripts\python.exe -m PyInstaller ^
  --onefile ^
  --name Auto-Concur ^
  --collect-submodules src ^
  --collect-all playwright ^
  --noupx ^
  --clean ^
  --noconfirm ^
  launcher.py
if errorlevel 1 goto fail

if not exist dist\Auto-Concur.exe goto missing

echo.
echo ================================================================
echo   Done. Hand over this one file:
echo.
dir /b dist\Auto-Concur.exe
echo   (full path: %CD%\dist\Auto-Concur.exe)
echo.
echo   Tell them to put it in ITS OWN FOLDER before running it.
echo   It creates settings.json, downloads and browser-profile
echo   right next to itself.
echo ================================================================
pause
exit /b 0

:missing
echo.
echo PyInstaller said it finished but dist\Auto-Concur.exe is not there.
echo An antivirus deleting the new exe is the usual cause. Check its
echo quarantine log.
goto fail

:pipfail
echo.
echo Could not install pyinstaller. A company proxy blocking pip is the
echo usual cause. Build on a PC with normal internet.
goto fail

:notyet
echo Not installed yet. Double-click setup.bat first.
goto fail

:fail
echo.
pause
exit /b 1
