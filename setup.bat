@echo off
rem ASCII only. Do not put Korean here.
rem cmd parses this file with whatever console codepage is active (949, 65001,
rem 437...). Any non-ASCII byte gets mis-decoded and the parser starts splitting
rem lines in the middle of tokens. We tried CP949 and UTF-8; both broke on some
rem machines. ASCII always works. Korean instructions live in README.md.
setlocal
cd /d "%~dp0"

echo ================================================================
echo   Concur expense automation - setup
echo ================================================================
echo.

call :findpy
if defined PY goto found

echo Python not found. Trying to install it.
echo.
winget --version >nul 2>&1
if errorlevel 1 goto nowinget

echo   Installing Python 3.12 with winget. This takes a few minutes...
rem --source winget matters. Without it winget also searches the Microsoft
rem Store source, which wants a region handshake first and dies with
rem "0x8a15000f : Data required by the source is missing" - taking the whole
rem install down with it even though the package we want is not in the Store.
winget install --id Python.Python.3.12 --source winget --scope user --silent --accept-package-agreements --accept-source-agreements
rem And check whether it worked. This used to fall through to :recheck no
rem matter what, so a failed winget printed "Python was installed but this
rem window cannot see it yet" - sending people to close and reopen a window
rem that was never going to help.
if errorlevel 1 goto wingetfailed
goto recheck

:nowinget
echo   winget is not on this PC. Downloading from python.org...
goto download

:wingetfailed
echo.
echo   winget could not install it. Falling back to python.org...
goto download

:download
set "INSTALLER=%TEMP%\python-setup.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile ($env:TEMP + '\python-setup.exe')"
if not exist "%INSTALLER%" goto nonet
rem Per-user install, so no administrator rights needed.
"%INSTALLER%" /quiet InstallLauncherAllUsers=0 PrependPath=1 Include_test=0
del "%INSTALLER%" >nul 2>&1
goto recheck

:nonet
echo.
echo   Download failed. Your company network may be blocking it.
echo   Install Python yourself from https://www.python.org/downloads/
echo   and CHECK "Add python.exe to PATH", then run setup.bat again.
goto fail

:recheck
call :findpy
if defined PY goto found
echo.
echo   Python was installed but this window cannot see it yet.
echo   Close this window and run setup.bat once more.
goto fail

:found
%PY% --version
echo.

if exist venv goto haveenv
echo Creating the virtual environment...
%PY% -m venv venv
if errorlevel 1 goto fail

:haveenv
echo Installing packages. This takes a few minutes...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if errorlevel 1 goto pipfail
venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 goto pipfail

rem The bundled Chromium is a nice-to-have, not a requirement. The program uses
rem the Edge or Chrome already on the PC. A company firewall often blocks
rem cdn.playwright.dev (connect EACCES) and that must not stop setup.
echo Downloading the browser (optional - Edge is used if this fails)...
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
echo   Could not download the browser - your company network blocks it.
echo   That is fine. Microsoft Edge on this PC will be used instead.
goto done

:pipfail
echo.
echo Could not install the packages.
echo A company proxy blocking pip is the usual cause.
echo Read the error message printed above.
goto fail

:fail
echo.
pause
exit /b 1

rem --- find python ---------------------------------------------------------
rem The py launcher is the most reliable. Fall back to python on PATH.
:findpy
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :eof
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
goto :eof
