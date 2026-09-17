# 2026-09-17 대상별 매칭·달력·단일 EXE 검증

## 검증 대상

- 검증된 실제 소스 commit: `653a4fd8fed2da034995c505c3e92c733aa01606`.
- 이 문서 추가와 검증용 생성 스크립트/임시 워크플로 정리는 위 소스·테스트·빌드 코드를 바꾸지 않는다.
- 달력과 목록 파서 개선은 실제 `src/` 파일에 적용되어 있다. 별도 패치 스크립트를 실행할 필요가 없다.

## 자동 검사 결과

| 환경 | 결과 |
|---|---|
| Linux + Tk/Xvfb + Chromium | 376 passed, 6 skipped |
| Windows + Python 3.12 + Chromium | 376 passed, 6 skipped, 0 failures, 0 errors |
| Windows PyInstaller 단일 EXE 빌드 | 성공 (`--onefile`) |
| 생성된 EXE 실행 검사 | 성공: 첫 화면 달력 2개, 작업지 숙박 달력 적용 |

6개 건너뜀은 기존 `tests/fixtures/sample_slip.pdf`가 없는 파서 회귀 검사다. 새 대상별 매칭 검사는 건너뛰지 않았다.
검증 결과 XML은 총 382개, skipped=6, failures=0, errors=0이다.
실행 검사 JSON: `smoke_test=passed`, `main_calendars=2`, `worksheet_calendar=true`, `concur_contacted=false`.

Windows 최초 시도에서는 달력 테스트 준비 중 Tk의 `ttk/notebook.tcl` 읽기 오류가 한 번 발생했다
(375 passed, 6 skipped, 1 setup error). 동일 소스의 두 번째 실행에서는 전체 검사와 단일 EXE
실행 검사가 통과했다. 최초 파일 읽기 오류의 원인을 제품 코드의 수정으로 해결했다고 주장하지 않는다.

- [Windows 검증 실행 기록 — 두 번째 시도](https://github.com/oks9924/Auto-Concur/actions/runs/35194944340)
- 두 번째 시도 job ID: `105116816285`
- 두 번째 시도 검증 artifact ID: `10485582970`

## 주요 회귀 사례

불완전하지만 날짜/금액으로 무관함이 확인되는 행의 제외, 관련 가능성이 있는 불완전 행에 대한
대상별 보류, 날짜·금액 모두 미확인, 중복 ID, 동일 날짜·금액의 구별 불가,
복수 원본의 후보 경합, 영수증/작업지 간 경합, 순서 독립성, 목록 로딩/미관측 행 차단,
실행 직전 새 모호성 재검증, 안전한 거래만 실제 작업 계획에 포함되는 부분 처리,
달력 입력·필터된 행에 적용·실행 취소를 검사했다.

## 검증하지 않은 것

이번 검사는 실제 Concur에 로그인하지 않았고 실제 경비의 첨부·저장·제출을 수행하지 않았다.
회사 PC의 보안 소프트웨어, SSO 환경, 실제 숙박 명세 화면, 실행 중단 후 실서비스 재개는
별도 환경 검증이 필요한 영역이다. 최초 오류가 수동 생성 경비 때문이었다고 확정하는 자료도 아니다.

단일 EXE 빌드: Windows에서 `setup.bat` 다음 `build_exe.bat` 실행.
결과물 `dist/Auto-Concur.exe`만 배포할 수 있다. 사용자 전표·작업지·진행 기록은 별도 데이터이며
기존 파일을 삭제하거나 새로 생성할 필요가 없다. 기존 EXE는 소스 업데이트만으로 바뀌지 않는다.
