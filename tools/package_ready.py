"""검증된 실행 폴더와 적용 완료 소스를 별도 ZIP으로 묶는다."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import shutil

root = Path(__file__).resolve().parents[1]
readme = '''Auto-Concur 달력 개선판 (2026.09.17)

실행 방법
1. ZIP의 전체 내용을 새 폴더에 풉니다.
2. Auto-Concur.exe를 더블클릭합니다. _internal 폴더를 함께 유지합니다.
3. [전표 폴더 - 찾아보기]로 기존 전표/workbook.json이 있는 폴더를 선택합니다.
4. 작업지 편집 또는 C. Concur 반영을 사용합니다.

Python, Excel, 별도 패키지 설치는 필요하지 않습니다.
Windows 64-bit와 설치된 Microsoft Edge 또는 Google Chrome이 필요합니다.
이 배포본은 코드서명되지 않았습니다. 회사 실행 정책이 차단하면 이를 우회하지 마세요.

변경 사항
- 첫 화면 조회 시작일/종료일: 입력칸 또는 달력 버튼을 누르면 날짜 선택창이 열립니다.
- 처음 기본 기간은 이번 달 1일~오늘이며, 실행 시 저장한 기간은 다음에 복원합니다.
- 작업지 입실날짜/퇴실날짜: 더블클릭/Enter 또는 [숙박 날짜 · 달력]으로 선택합니다.
- 숙박 날짜 선택은 적용 전까지 작업지를 변경하지 않으며 Ctrl+Z로 되돌릴 수 있습니다.
- 원본 거래일·금액·가맹점은 계속 읽기 전용입니다.
- Concur 날짜 형식·금액 공백 처리와 실제 표 머리글 기반 읽기를 보완했습니다.
- 불완전/모호한 행, 중복 경비 ID는 강제로 매칭하지 않습니다.
- 기존 빈칸 유지, 영수증 중복 방지, 시작 전 확인, 저장 후 검증은 유지합니다.

기존 EXE를 실행하면 이전 코드가 동작합니다. 이 폴더의 새 EXE를 실행하세요.
기존 전표/workbook.json/concur-progress.json은 지우거나 초기화할 필요가 없습니다.
프로그램은 설정을 새 실행 폴더에 저장합니다. 이전 설정 자동 복사는 하지 않습니다.

검증 범위
Windows 자동 테스트 및 실제 EXE의 창/달력/작업지 시작 검증을 빌드에서 수행합니다.
테스트에는 합성 HTML/거래만 사용합니다. 실제 Concur 로그인·첨부·저장은 검증하지 않았습니다.
'''
dist = root/'dist'/'Auto-Concur'
(dist/'사용방법.txt').write_text(readme,encoding='utf-8-sig')
for path in dist.rglob('*'):
    if path.is_file() and path.suffix.lower() in {'.ttf','.otf','.ttc','.woff','.woff2'}:
        path.unlink()
for name in ('startup-error.txt','smoke-result.json'):
    path = dist/name
    if path.exists():
        shutil.copy2(path,root/name)
        path.unlink()
with ZipFile(root/'Auto-Concur-Windows-20260917.zip','w',ZIP_DEFLATED) as bundle:
    for path in sorted(dist.rglob('*')):
        if path.is_file():
            bundle.write(path,Path('Auto-Concur')/path.relative_to(dist))
with ZipFile(root/'Auto-Concur-Source-20260917.zip','w',ZIP_DEFLATED) as bundle:
    for folder in ('src','tests','docs'):
        for path in sorted((root/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                bundle.write(path,Path('Auto-Concur-Source')/path.relative_to(root))
    for pattern in ('*.py','*.bat','requirements.txt','README.md'):
        for path in sorted(root.glob(pattern)):
            bundle.write(path,Path('Auto-Concur-Source')/path.name)
    bundle.writestr('Auto-Concur-Source/이번수정.txt', readme)
