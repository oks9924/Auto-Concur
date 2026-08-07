@echo off
rem CP949로 저장한다. setup.bat 과 같은 이유다.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe goto notyet
rem python.exe 로 띄운다. pythonw 는 창이 안 뜨는 대신 오류도 안 보인다.
venv\Scripts\python.exe -m src.gui
exit /b 0

:notyet
echo 아직 설치가 안 됐습니다. setup.bat 을 먼저 더블클릭해 주세요.
pause
exit /b 1
