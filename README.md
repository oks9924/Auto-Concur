# concur-expense-automation

현대카드 매출전표를 받아서 Concur 경비 처리까지 이어지는 반복 작업을 줄인다.

## 전체 흐름

```
[A] 현대카드(mycompany) → 월별 전표 PDF 다운로드          ← 미구현
     ↓
[B] PDF 파싱 → 이름 변경 + manifest.csv                   ← 구현 완료
     ↓  여기서 사람이 눈으로 확인한다
[C] Concur → 날짜/금액 매칭 → PDF 첨부                    ← 미구현
     ↓
[D] 식사·카페 건: 경비유형/참석자/목적/코멘트 입력         ← 미구현
```

**단계를 일부러 쪼갰다.** 통짜 스크립트로 만들면 어디서 깨졌는지 알 수 없고,
C가 실패했을 때 A부터 다시 돌려야 한다. `manifest.csv`가 B와 C 사이의 인계물이고,
중간에 사람이 검수하는 지점이다.

## 환경

**Windows 기준으로 만들었다.** A단계에서 현대카드 기업포털을 다뤄야 하는데
한국 기업/금융 포털은 Windows 전제인 경우가 많아서다. macOS/Linux에서도
B단계(파싱·정리)는 그대로 돌아간다.

Python 3.10 이상이 필요하다.

## 지금 되는 것: B단계

```bat
py -m venv venv
venv\Scripts\pip install -r requirements.txt

venv\Scripts\python -m src.organize .\downloads            :: 미리보기
venv\Scripts\python -m src.organize .\downloads --apply    :: 실제 이름 변경
```

macOS/Linux는 `venv\Scripts\` 대신 `venv/bin/` 을 쓴다.

전표 PDF를 모아둔 폴더를 주면:

- `20260630_22900_00099016.pdf` (날짜_금액_승인번호) 형식으로 이름을 바꾸고
- `manifest.csv`를 만든다 — 거래일, 거래시각, 합계, 승인번호, 가맹점명, 카드번호 등

기본은 미리보기다. `--apply`를 붙여야 실제로 바뀐다. 두 번 돌려도 안전하다.

## 알아둘 것

**파일명 날짜를 믿으면 안 된다.** 현대카드가 내려주는 파일명(`..._20260711_43.pdf`)의
날짜는 실제 거래일과 다르다. 샘플의 경우 파일명은 `20260711`인데 거래일시는
`2026/06/30 20:59:44`였다. 그래서 파일명이 아니라 PDF 내용을 파싱한다.

**승인번호가 고유키다.** 같은 날 같은 금액의 결제가 둘 이상일 수 있다(커피 두 잔).
날짜+금액만으로 매칭하면 엉뚱한 전표가 첨부되고, 이건 나중에 감사에서 문제가 된다.
C단계에서 매칭이 모호하면 **건너뛰고 사람에게 넘긴다** — 자동으로 아무거나 붙이지 않는다.

**파싱은 실패하면 크게 실패한다.** 값을 확신할 수 없으면 추측하지 않고
`SlipParseError`를 던지고 그 파일은 이름을 바꾸지 않는다. 잘못 읽은 전표가
조용히 섞여 들어가는 것이 파싱 실패보다 훨씬 나쁘다.

**금액 검산.** `금액 + 부가세 + 봉사료 == 합계`가 안 맞으면 파싱이 틀린 것으로 보고 실패시킨다.

## 전표 PDF 구조

'카드매출전표 인터넷 재발급용' 양식은 **폼 라벨이 배경 JPEG에 그려져 있고 값만
텍스트 레이어에 있다.** 그래서 `거래일시:` 같은 문자열로 값을 찾을 수 없다.
596x843pt 페이지에 27.68pt 간격의 고정 그리드로 값이 배치되므로 좌표로 읽는다
(`src/slip_parser.py`의 `ROWS`).

## 다음 단계 (A, C, D)

전부 Playwright 브라우저 자동화다. Concur API는 **Client Web Services 라이선스
별도 구매 + 회사 관리자 권한**이 필요해서 개인이 쓸 수 없다.

**로그인은 사람이 한다.** 간편인증·OTP·SSO는 자동화 대상이 아니다.
`browser-profile/`에 세션이 남으므로 다음 실행부터는 로그인이 유지된다.

셀렉터는 추측하지 않는다. 실제 화면을 떠서 확인한 뒤에 쓴다:

```bat
venv\Scripts\playwright install chromium

venv\Scripts\python -m src.inspect_page "https://mycompany.hyundaicard.com/hs/cs/HSCS1002.do" --name hyundaicard

venv\Scripts\python -m src.inspect_page "https://eu2.concursolutions.com/home" --name concur
```

브라우저가 뜨면 로그인하고 원하는 화면까지 이동한 뒤 Enter를 누르면
`inspect-out/<name>/`에 화면 캡처, 프레임별 HTML, 클릭 가능한 요소 목록이 저장된다.

## 커밋하지 않는 것

실제 카드 데이터는 저장소에 올리지 않는다 (`.gitignore`):
`downloads/`, `tests/fixtures/*.pdf`, `manifest.csv`, `browser-profile/`, `inspect-out/`

테스트는 `tests/fixtures/sample_slip.pdf`가 있으면 돌고, 없으면 건너뛴다.

```bat
venv\Scripts\python -m pytest tests\ -q
```

## Windows에서 걸릴 만한 것

**콘솔 한글 출력.** 파이썬은 Windows 콘솔 코드페이지로 stdout을 인코딩한다.
영문 Windows(cp1252)에서는 한글을 찍는 순간 `UnicodeEncodeError`로 죽는다.
이름 변경 도중에 죽으면 어디까지 바뀌었는지 알기 어려우므로 진입 시점에
UTF-8로 맞춘다 (`src/console.py`). 한국어 Windows(cp949)는 원래 문제없다.

**파일이 열려 있으면 이름이 안 바뀐다.** PDF 뷰어로 전표를 열어둔 채 `--apply`를
돌리면 그 파일만 `PermissionError`로 실패 목록에 남는다. 뷰어를 닫고 다시 돌리면 된다.

**CSV 한글.** `manifest.csv`는 utf-8-sig로 쓴다. 엑셀에서 바로 열어도 안 깨진다.
