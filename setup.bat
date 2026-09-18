@echo off
rem ASCII only. Do not put Korean here.
rem This project is tested on Python 3.12. Do not silently use another major/minor.
setlocal
cd /d "%~dp0"

echo ================================================================
echo   Concur expense automation - setup
echo   Required runtime: Python 3.12
echo ================================================================
echo.

call :findpy
if defined PY goto found

echo Python 3.12 not found. Trying to install it.
echo.
winget --version >nul 2>&1
if errorlevel 1 goto nowinget

echo   Installing Python 3.12 with winget. This takes a few minutes...
winget install --id Python.Python.3.12 --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto wingetfailed
goto recheck

:nowinget
echo   winget is not on this PC. Downloading Python 3.12.7 from python.org...
goto download

:wingetfailed
echo.
echo   winget could not install Python 3.12. Falling back to python.org...
goto download

:download
set "INSTALLER=%TEMP%\python-3.12-setup.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile ($env:TEMP + '\python-3.12-setup.exe')"
if not exist "%INSTALLER%" goto nonet
"%INSTALLER%" /quiet InstallLauncherAllUsers=0 PrependPath=1 Include_test=0
del "%INSTALLER%" >nul 2>&1
goto recheck

:nonet
echo.
echo   Download failed. Install Python 3.12 manually, then run setup.bat again.
goto fail

:recheck
call :findpy
if defined PY goto found
echo.
echo   Python 3.12 was installed but this window cannot see it yet.
echo   Close this window and run setup.bat once more.
goto fail

:found
echo Using:
%PY% --version
echo.

if not exist venv\Scripts\python.exe goto makeenv
venv\Scripts\python.exe -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 goto haveenv

echo Existing venv is not Python 3.12 and will be recreated.
venv\Scripts\python.exe --version 2>nul
rmdir /s /q venv
if exist venv goto envlocked

:makeenv
echo Creating the Python 3.12 virtual environment...
%PY% -m venv venv
if errorlevel 1 goto fail

:haveenv
echo Installing pinned packages. This takes a few minutes...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if errorlevel 1 goto pipfail
venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 goto pipfail

venv\Scripts\python.exe -c "import sys,importlib.metadata as m; assert sys.version_info[:2] == (3,12); assert m.version('playwright') == '1.63.0'; print('Runtime:', sys.version.split()[0], 'Playwright', m.version('playwright'))"
if errorlevel 1 goto pipfail

echo Downloading the Playwright 1.63 Chromium build...
venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto nobrowser

:done
echo.
echo ================================================================
echo   Setup finished. Now double-click run.bat
echo ================================================================
pause
exit /b 0

:nobrowser
echo.
echo   Chromium download failed. The program can still try installed Edge/Chrome.
goto done

:envlocked
echo.
echo Could not replace venv. Close Auto-Concur and any terminals using this venv,
echo then run setup.bat again.
goto fail

:pipfail
echo.
echo Could not install or verify the pinned runtime packages.
echo Read the error message printed above.
goto fail

:fail
echo.
pause
exit /b 1

rem --- find exactly Python 3.12 -------------------------------------------
:findpy
set "PY="
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3.12"
if defined PY goto :eof
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
goto :eof
