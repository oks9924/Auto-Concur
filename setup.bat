@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ================================================================
echo   Concur 경비 자동화 - 설치
echo ================================================================
echo.

rem --- 1. Python 찾기 -------------------------------------------------------
rem py 런처가 있으면 그것이 가장 확실하다. 없으면 python 을 본다.
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY python --version >nul 2>&1 && set PY=python

if not defined PY (
    echo Python이 없습니다. 설치를 시도합니다.
    echo.

    rem winget 은 Windows 10 최신판부터 기본으로 들어 있다. 있으면 이걸로 끝난다.
    winget --version >nul 2>&1
    if !errorlevel! equ 0 (
        echo   winget으로 Python 3.12를 설치합니다. 몇 분 걸립니다...
        winget install --id Python.Python.3.12 --scope user --silent ^
            --accept-package-agreements --accept-source-agreements
    ) else (
        echo   winget이 없어서 python.org에서 직접 받습니다...
        set INSTALLER=%TEMP%\python-setup.exe
        powershell -NoProfile -Command ^
            "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\python-setup.exe'"
        if not exist "!INSTALLER!" (
            echo.
            echo   내려받지 못했습니다. 회사 네트워크가 막고 있을 수 있습니다.
            echo   https://www.python.org/downloads/ 에서 직접 설치한 뒤 다시 실행해 주세요.
            echo   설치할 때 'Add python.exe to PATH' 를 꼭 체크해 주세요.
            pause
            exit /b 1
        )
        rem 사용자 계정에만 설치하므로 관리자 권한이 필요 없다.
        "!INSTALLER!" /quiet InstallLauncherAllUsers=0 PrependPath=1 Include_test=0
        del "!INSTALLER!" >nul 2>&1
    )

    rem 방금 설치한 것은 이 창의 PATH에 아직 없다. 런처를 다시 찾는다.
    set PY=
    py -3 --version >nul 2>&1 && set PY=py -3
    if not defined PY python --version >nul 2>&1 && set PY=python
    if not defined PY (
        echo.
        echo   설치는 됐지만 이 창에서 찾지 못했습니다.
        echo   이 창을 닫고 setup.bat 을 한 번 더 실행해 주세요.
        pause
        exit /b 1
    )
)

for /f "tokens=*" %%v in ('%PY% --version') do echo Python: %%v
echo.

rem --- 2. 가상환경 ----------------------------------------------------------
if not exist venv (
    echo 가상환경을 만듭니다...
    %PY% -m venv venv || goto :fail
)

echo 필요한 것들을 설치합니다. 몇 분 걸립니다...
venv\Scripts\python -m pip install --upgrade pip --quiet || goto :fail
venv\Scripts\pip install -r requirements.txt --quiet || goto :fail

echo 브라우저를 받습니다...
venv\Scripts\playwright install chromium || goto :fail

echo.
echo ================================================================
echo   설치가 끝났습니다.
echo   이제 run.bat 을 더블클릭하면 창이 뜹니다.
echo ================================================================
pause
exit /b 0

:fail
echo.
echo 설치 중에 문제가 생겼습니다. 위에 찍힌 메시지를 확인해 주세요.
echo 회사 네트워크가 pip 를 막는 경우가 있습니다.
pause
exit /b 1
