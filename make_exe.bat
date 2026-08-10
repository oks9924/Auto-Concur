@echo off
rem ASCII only - same reason as setup.bat.
rem
rem Makes Auto-Concur.exe RIGHT HERE, next to run.bat.
rem
rem This is not PyInstaller. It builds a few-KB shim that starts the venv's
rem python with the arguments we cannot otherwise pass. Use it where only an
rem executable can be registered and no arguments are accepted (DuoNX).
rem
rem The C# compiler used here ships with Windows. Nothing to install, no pip,
rem so this works on a locked-down PC where build_exe.bat cannot even start.
setlocal
cd /d "%~dp0"

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if exist "%CSC%" goto build
set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if exist "%CSC%" goto build
goto nocsc

:build
echo Building Auto-Concur.exe ...
"%CSC%" /nologo /target:exe /out:Auto-Concur.exe launcher.cs
if errorlevel 1 goto fail

echo.
echo ================================================================
echo   Done: Auto-Concur.exe
echo.
echo   Leave it in THIS folder. It runs the venv and the source that
echo   sit next to it, so it cannot be copied away on its own.
echo   Register this exe in DuoNX.
echo ================================================================
pause
exit /b 0

:nocsc
echo.
echo Could not find the Windows C# compiler:
echo   %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
echo Your Windows may not have the .NET Framework 4 files.
goto fail

:fail
echo.
pause
exit /b 1
