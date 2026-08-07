@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo 아직 설치가 안 됐습니다. setup.bat 을 먼저 더블클릭해 주세요.
    pause
    exit /b 1
)

venv\Scripts\python -m src.gui
