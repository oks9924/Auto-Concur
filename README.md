# concur-expense-automation

현대카드 매출전표를 받아서 Concur 경비 처리까지 이어지는 반복 작업을 줄인다.

## 전체 흐름

```
[A] 현대카드(mycompany) → 월별 전표 PDF 다운로드          ← 구현, 실행 검증 전
     ↓
[B] PDF 파싱 → 이름 변경 + manifest.csv                   ← 구현 완료
     ↓  여기서 사람이 눈으로 확인한다
[C] Concur → 날짜/금액 매칭 → PDF 첨부                    ← 매칭 구현, 첨부는 검증 전
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

## 설치

```bat
py -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\playwright install chromium
```

macOS/Linux는 `venv\Scripts\` 대신 `venv/bin/` 을 쓴다.

## A단계: 전표 다운로드

```bat
:: 처음엔 몇 건만 받아서 확인한다
venv\Scripts\python -m src.download_slips --from 2026.07.01 --to 2026.07.31 --limit 3

:: 확인됐으면 전체
venv\Scripts\python -m src.download_slips --from 2026.07.01 --to 2026.07.31
```

브라우저가 뜨면 **카드 인증을 직접 한다.** 카드번호·유효기간·CVV·비밀번호가 전부
가상키패드라 스크립트로 값을 넣을 수 없다. 금융 보안장치를 우회할 생각도 하지 않는다.
인증 후 Enter를 누르면 조회·선택·다운로드를 자동으로 돈다.

**전체를 한 번에 선택해 합본 PDF로 받고 페이지 단위로 쪼갠다.** `fnPdf()`가 띄우는
출력방식 모달에서 '페이지 당 1매씩'을 고르면 한 페이지가 전표 한 장이다.

한 건씩 받으면 안 된다. 서버가 주는 파일명이 매번 `매출전표_<오늘날짜>.pdf`로
**전부 같아서 서로 덮어쓴다**. 거래일이 아니라 내려받은 날짜가 들어간다.

페이지 순서는 신경 쓰지 않는다. 파일 이름을 PDF 내용(거래일시·금액·승인번호)에서
만들기 때문에 그리드 순서와 어긋나도 결과가 틀어지지 않는다.

**페이지 수와 건수가 다르면 멈춘다.** 48건을 선택했는데 47페이지가 오면 1:1 대응이
깨진 것이고, 그 상태로 진행하면 엉뚱한 전표가 첨부된다.

화면 셀렉터 (2026-08 확인):

| 요소 | 셀렉터 |
|---|---|
| 조회 시작일 / 종료일 | `#inqFromDt` / `#inqToDt` (`2026.07.01` 형식) |
| 조회 | `#btnIqry` |
| 매출전표 다운로드 | `button[onclick="fnPdf();"]` |
| 거래내역 xls | `#btnExcel` |

표는 **dhtmlxGrid**라서 일반 체크박스가 아니다(`eXcell_ch` 셀). 전역 그리드 객체
이름을 추측하지 않고, `getAllRowIds`를 가진 객체를 실행 시점에 찾아 쓴다.
체크박스 칼럼도 인덱스를 고정하지 않고 `getColType(i) === 'ch'` 로 찾는다.

## B단계: 파싱 + 정리

```bat
venv\Scripts\python -m src.organize .\downloads            :: 미리보기
venv\Scripts\python -m src.organize .\downloads --apply    :: 실제 이름 변경
```

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

## C단계: Concur 첨부

```bat
venv\Scripts\python -m src.attach_receipts                    :: 매칭 계획만 (아무것도 안 바꿈)
venv\Scripts\python -m src.attach_receipts --apply --limit 1  :: 한 건만 붙여서 확인
venv\Scripts\python -m src.attach_receipts --apply            :: 나머지 전부
```

SSO 로그인과 리포트 열기는 사람이 한다. 경비 목록이 보이는 상태에서 Enter를
누르면 목록을 읽어 `manifest.csv`와 맞춰본다.

**매칭 규칙: 금액이 정확히 같고 거래일이 ±1일 안. 후보가 정확히 하나일 때만
붙인다.** 없거나 둘 이상이면 건너뛰고 사람에게 넘긴다. 커피 두 잔처럼 같은 날
같은 금액이 둘이면 자동으로 아무거나 붙이면 안 된다.

±1일을 두는 이유는 카드 매입 처리 때문이다. 06/30 20:59 결제가 Concur에는
07/01로 들어올 수 있다.

**붙이기 직전에 한 번 더 확인한다.** 행을 클릭해 상세로 들어간 뒤 화면의
`#transactionAmount` 값이 전표 금액과 같은지 보고, 다르면 첨부하지 않는다.
엉뚱한 경비를 열었을 가능성을 막는 마지막 관문이다.

**두 번 돌려도 안전하다.** 붙인 승인번호를 `attached.txt`에 남겨서 다음 실행 때
건너뛴다. 안 그러면 같은 경비에 영수증이 겹쳐 붙는다.

화면 셀렉터 (2026-08 확인):

| 요소 | 셀렉터 |
|---|---|
| 영수증 파일 input | `#upload-file` |
| 금액 | `#transactionAmount` |
| 거래일 | `#transactionDate-date-input-field-input` |
| 공급업체 | `#vendorName` |
| 사업 목적 | `#businessPurpose` |
| 경비 저장 | `button` "경비 저장" |

파일 input이 DOM에 그대로 있어서 **파일 선택 대화상자를 다룰 필요가 없다.**
`set_input_files()`로 바로 넣는다.

**행의 id가 곧 경비 ID다.** 상세 주소가 `.../reports/{리포트}/expenses/{경비}` 라서
주소를 직접 만들 수 있다. 행을 클릭해서 여는 방식은 버렸다 — 행 안의 버튼은
알림(오류/경고)이나 카드 버튼이라 누르면 팝오버만 열리고 상세는 안 열린다.

데이터 행은 `[role="row"][data-testid="data-row"]` 로 고른다. `role="row"` 만 보면
헤더와 합계 행이 섞여서 인덱스가 어긋난다. 값은 `data-nuiexp` 훅에서 직접 읽는다
(`date-cell`, `amount-cell`, `vendor-name`, `expense-type-cell`). 칼럼 순서를
짐작하지 않아도 된다.

## 다음 단계 (D)

Concur도 Playwright 브라우저 자동화다. API는 **Client Web Services 라이선스
별도 구매 + 회사 관리자 권한**이 필요해서 개인이 쓸 수 없다.

**로그인은 사람이 한다.** 간편인증·OTP·SSO는 자동화 대상이 아니다.
`browser-profile/`에 세션이 남으므로 다음 실행부터는 로그인이 유지된다.

셀렉터는 추측하지 않는다. 실제 화면을 떠서 확인한 뒤에 쓴다:

```bat
venv\Scripts\python -m src.inspect_page "https://travel.siemens.cloud" --name concur
```

브라우저가 뜨면 로그인하고 원하는 화면까지 이동한다. 화면마다 Enter를 누르면
그 시점이 `inspect-out/<name>/01`, `02`... 로 저장된다. 끝내려면 `q` + Enter.
팝업 창까지 전부 저장하므로 전표 인쇄 같은 새 창도 잡힌다.

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
