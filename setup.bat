@echo off
rem 이 파일은 CP949로 저장한다. cmd는 시스템 코드페이지로 읽기 때문에
rem UTF-8로 저장하면 한글이 깨지고, 깨진 글자를 명령으로 해석하다 무너진다.
rem 괄호 블록과 지연 확장도 쓰지 않는다. goto/call 로만 흐름을 만든다.
setlocal
cd /d "%~dp0"

echo ================================================================
echo   Concur 경비 자동화 - 설치
echo ================================================================
echo.

call :findpy
if defined PY goto found

echo Python이 없습니다. 설치를 시도합니다.
echo.
winget --version >nul 2>&1
if errorlevel 1 goto download

echo   winget으로 Python을 설치합니다. 몇 분 걸립니다...
winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
goto recheck

:download
echo   winget이 없어서 python.org에서 직접 받습니다...
set "INSTALLER=%TEMP%\python-setup.exe"
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile ($env:TEMP + '\python-setup.exe')"
if not exist "%INSTALLER%" goto nonet
rem 사용자 계정에만 설치하므로 관리자 권한이 필요 없다.
"%INSTALLER%" /quiet InstallLauncherAllUsers=0 PrependPath=1 Include_test=0
del "%INSTALLER%" >nul 2>&1
goto recheck

:nonet
echo.
echo   내려받지 못했습니다. 회사 네트워크가 막고 있을 수 있습니다.
echo   https://www.python.org/downloads/ 에서 직접 설치한 뒤 다시 실행해 주세요.
echo   설치할 때 'Add python.exe to PATH' 를 꼭 체크해 주세요.
goto fail

:recheck
call :findpy
if defined PY goto found
echo.
echo   설치는 됐지만 이 창에서 찾지 못했습니다.
echo   이 창을 닫고 setup.bat 을 한 번 더 실행해 주세요.
goto fail

:found
%PY% --version
echo.

if exist venv goto haveenv
echo 가상환경을 만듭니다...
%PY% -m venv venv
if errorlevel 1 goto fail

:haveenv
echo 필요한 것들을 설치합니다. 몇 분 걸립니다...
venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if errorlevel 1 goto pipfail
venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
if errorlevel 1 goto pipfail

echo 브라우저를 받습니다...
venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto fail

echo.
echo ================================================================
echo   설치가 끝났습니다.
echo   이제 run.bat 을 더블클릭하면 창이 뜹니다.
echo ================================================================
pause
exit /b 0

:pipfail
echo.
echo 패키지를 받지 못했습니다. 흔한 이유 두 가지입니다.
echo   1. 회사 네트워크가 pip 를 막는다 - 사내 프록시 설정을 확인해 주세요.
echo   2. Python 이 너무 최신이다 - 3.13 이상은 아직 준비 안 된 패키지가 있습니다.
echo      그때는 3.12 를 설치하고 venv 폴더를 지운 뒤 다시 실행해 주세요.
goto fail

:fail
echo.
pause
exit /b 1

rem --- Python 찾기 ---------------------------------------------------------
rem py 런처가 있으면 그것이 가장 확실하다. 없으면 python 을 본다.
:findpy
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :eof
python --version >nul 2>&1
if not errorlevel 1 set "PY=python"
goto :eof
