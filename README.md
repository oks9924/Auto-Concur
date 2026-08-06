# concur-expense-automation

현대카드 매출전표를 받아서 Concur 경비 처리까지 이어지는 반복 작업을 줄인다.

## 전체 흐름

```
[A] 현대카드(mycompany) → 월별 전표 PDF 다운로드          ← 실측 검증 완료 (2026-07, 48건)
     ↓
[B] PDF 파싱 → 이름 변경 + 작업지(csv/xlsx)               ← 실측 검증 완료
     ↓  여기서 엑셀로 경비유형·목적·코멘트·참석자를 건별로 손본다
     ↓  여기서 사람이 눈으로 확인한다
[C] Concur 반영 — 영수증 첨부 + 유형·목적·코멘트·참석자   ← 실측 검증 완료
     (한 세션에서 이어서 한다. 로그인은 한 번)
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
- `manifest.csv` 와 `manifest.xlsx` 를 만든다 — 거래일, 거래시각, 금액, 승인번호 등
- 끝나면 그 폴더를 탐색기로 연다

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

## C단계: Concur 반영

```bat
venv\Scripts\python -m src.update_concur                  :: 계획만
venv\Scripts\python -m src.update_concur --apply --limit 1 :: 한 건만
venv\Scripts\python -m src.update_concur --apply           :: 전부
```

영수증 첨부와 유형·목적·코멘트·참석자 입력을 **한 세션에서 이어서** 한다.
따로 돌리면 브라우저를 두 번 띄우고 로그인·리포트 열기를 두 번 해야 한다.

작업지(`manifest.xlsx` 또는 `manifest.csv`)가 있으면 거기 적힌 대로 넣는다.
없으면 규칙대로 한다. 나눠서 돌리려면 `attach_receipts` 와 `fix_expenses` 를
그대로 쓸 수 있다.

### 영수증 첨부

```bat
venv\Scripts\python -m src.attach_receipts                    :: 매칭 계획만 (아무것도 안 바꿈)
venv\Scripts\python -m src.attach_receipts --apply --limit 1  :: 한 건만 붙여서 확인
venv\Scripts\python -m src.attach_receipts --apply            :: 나머지 전부
```

SSO 로그인과 리포트 열기는 사람이 한다. 경비 목록이 보이는 상태에서 Enter를
누르면 목록을 읽어 `manifest.csv`와 맞춰본다.

**매칭 규칙: 금액이 정확히 같고 거래일이 ±1일 안.** 후보가 여럿이면 가맹점명으로
가른다. manifest는 한글(`라한호텔울산`), Concur는 로마자(`RA HAN HO TEL UL SAN`)라
옮겨서 견준다. 실측으로 같은 가게는 1.00, 다른 가게는 0.3 미만이었다.

가맹점으로도 안 갈리면 앞에서부터 순서대로 배정한다. 날짜와 금액이 같으면
어느 쪽이든 된다고 보기로 했다. 다만 어떻게 정했는지는 출력에 표시한다
(`[가맹점으로 판별]`, `[순서 배정 - 확인 권장]`).

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

## D단계: 경비유형 · 참석자 · 목적 · 코멘트

두 가지로 쓸 수 있다.

**작업지대로 (권장)** — `organize`가 만든 `manifest.xlsx`를 엑셀에서 손본 뒤 넘긴다.
건별로 다르게 지정할 수 있고, 넣기 전에 눈으로 다 볼 수 있다.
`update_concur` 는 작업지가 있으면 알아서 쓴다.

작업지에는 손댈 칸만 보인다. 파일명·가맹점명·거래유형·카드번호·사업자등록번호·
전표번호·원본파일명은 **숨겨두되 지우지는 않는다** — 파일명은 첨부할 PDF를 찾는 데,
가맹점명은 후보가 여럿일 때 어느 경비인지 가리는 데 쓰고, 나머지도 나중에 근거를
되짚을 때 필요하다.

**경비유형·목적·코멘트·참석자는 비어 있는 채로 나온다.** 미리 채워주지 않는다.
Concur에 들어갈 값은 작업지에 적힌 것뿐이다. 다시 돌려도 앞서 손으로 적은 값은
승인번호로 기억해 그대로 둔다.

**참석자는 쉼표로 여러 명** 적을 수 있다(`kyungsik.oh, hong.gildong`). 한 명씩
검색·추가하고 검색어를 지운 뒤 다음 사람으로 넘어간다.

경비유형을 `내부 직원간 식음료`로 고르면 그 행의 참석자 칸이 초록으로 바뀐다.
그 유형은 참석자가 있어야 하는데 빈 칸은 눈에 안 띄어서 빠뜨리기 쉽다.

```bat
venv\Scripts\python -m src.fix_expenses --sheet                    :: 계획만
venv\Scripts\python -m src.fix_expenses --sheet --apply            :: 반영
venv\Scripts\python -m src.fix_expenses --sheet downloads\manifest.xlsx --apply
```

**경비유형 칸은 드롭다운에서만 고를 수 있다.** 목록 밖의 값은 엑셀이 경고가 아니라
거부로 막는다. 오타로 없는 유형을 적으면 코드를 못 찾기 때문이다. 목록은 숨긴
시트에 두고 참조한다 — 수식에 직접 넣으면 255자 제한에 걸린다(한글 유형명이 길다).

빈 칸은 건드리지 않는다. 예를 들어 숙박비 건은 참석자·목적·코멘트를 비워두면
유형만 바꾸고 나머지는 그대로 둔다.

**규칙대로 (작업지 없이)** — `--sheet` 없이 돌리면 아래 규칙을 쓴다.

```bat
venv\Scripts\python -m src.fix_expenses --apply --limit 1
```

| 조건 | 처리 |
|---|---|
| 금액 >= 100,000 | 경비유형을 숙박비로. 참석자·목적·코멘트는 넣지 않는다 |
| `개인 식사 (SISW Only)` | `내부 직원간 식음료` + 참석자·목적·코멘트 |
| 이미 `내부 직원간 식음료` | 참석자·목적·코멘트만 채운다 |
| 그 외 (환급 불가, 대중교통 등) | 건드리지 않는다 |

**이미 채워진 값은 덮어쓰지 않는다.** 참석자가 이미 있으면 추가하지 않는다.
그래서 두 번 돌려도 안전하고 중간에 끊겨도 이어서 하면 된다.

경비 유형 옵션은 id에 코드가 박혀 있다(`01182` = 내부 직원간 식음료,
`LODNG` = 숙박비). 앞부분은 React가 만들어 매번 바뀌므로 코드만 보고 잡는다.

참석자 모달은 `?modal=attendees&context=entry` 로 주소로 열 수 있다.

**새 경비유형(택시 등)이 생기면** 코드를 뽑아 `settings.json`에 넣는다.

```bat
venv\Scripts\python -m src.fix_expenses --list-types
```

참고로 택시는 이미 `대중교통비(지하철, 버스, 기차, 택시, 통행료 등)`(`TRAIN`)에 들어 있다.

## 설정과 창

```bat
venv\Scripts\python -m src.gui
```

전표 폴더는 **찾아보기로 고른다.** 세 단계가 모두 그 폴더를 쓴다.

창에는 큰 금액 경비유형·임계금액·날짜 허용 오차만 있다. **목적·코멘트·참석자는
창에 없다** — 어차피 작업지(엑셀)에서 건별로 정하기 때문이다. 다만 그 값들이
작업지 미리채움의 출처라서 `settings.json`에는 남아 있다. 바꿀 일이 생기면
그 파일을 직접 고치면 된다.

단계는 새 콘솔 창에서 돈다. 카드 인증이나 로그인을 마치고 Enter를 눌러야 하는
대기가 있어서, 창 안에 출력을 가두면 그 조작을 할 수 없다.

다운로드와 정리가 끝나면 **전표 폴더가 자동으로 열린다.** 결과를 바로 볼 수 있다.

## 매달 하는 일

```bat
venv\Scripts\python -m src.download_slips --from 2026.08.01 --to 2026.08.31
venv\Scripts\python -m src.organize .\downloads --apply
:: manifest.xlsx 를 엑셀에서 훑어본 뒤
venv\Scripts\python -m src.update_concur --apply
```

사람이 하는 것은 현대카드 카드 인증과 Concur 로그인뿐이다.
각 단계는 계획 출력(`--apply` 없이)으로 먼저 확인할 수 있고, 두 번 돌려도 안전하다.

## 화면이 바뀌어 깨지면

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
