# Concur가 실제로 쓰는 계층 (2026-11-05 실측)

`python -m src.record_calls` 1회차 기록(168줄 / 호출 162건)에서 나온 것. 값은 기록하지
않았고 모양만 봤다. 원본은 각자 PC의 `inspect-out/calls-*.jsonl`(커밋하지 않는다).

## 한 줄 결론

**찾던 JSON 계층이 있다. GraphQL이다.** 지금 C단계가 화면에서 긁어내려고 쓰는 코드의
대부분은 한 계층 아래에서 기계값으로 그냥 온다.

| | |
|---|---|
| 경비 데이터 | `www-eu2.api.concursolutions.com/spend-graphql/graphql` (78회) |
| 공통 데이터 | `/cds/graphql` (5회) |
| 영수증 업로드 | `/spend-graphql/upload` → 202 (1회) |
| 카드 피드 목록 | `/smartexpense/v4/users/{id}/context/TRAVELER/smartexpenses` |
| **구형 .NET** | `eu2.concursolutions.com/expense/expenseDotNet/Proxy/expenseRouter.ashx` (17회) |

## 날짜는 ISO다 — 추론 기계가 필요 없다

| 키 | 관측 | 종류 |
|---|---|---|
| `transactionDate` | 43회 | `date-iso` |
| `hotelCheckinDate` | 7회 | `date-iso` |
| `hotelCheckoutDate` | 7회 | `date-iso` |
| `reportDate` | 1회 | `date-iso` |

**168줄 전체에 `date-slash`·`date-dot`이 하나도 없다.** `10/09/2026` 모호성은 표현
계층에서만 생긴다. 숙박 입실·퇴실도 `hotelCheckinDate`/`hotelCheckoutDate` 라는
독립 필드로, 날짜 범위 입력칸 하나에 서식 맞춰 넣는 일이 아니다.

## 금액은 정수 + 통화코드다

`{"value": int, "currencyCode": "KRW"}` 꼴. `value: int` 109회, `currencyCode` 122회.
**`number-grouped`(`1,234.00` 꼴)가 0건이다.** `1,234.00` vs `1.234,00` 설정이 무의미해진다.

## 작전 목록 (응답 `data` 하위 키 = 작전 이름)

읽기: `reportEntriesDetails`, `employee.expenseReport.entry`, `existingExpenseForm`,
`newExpenseForm`, `expenseTypesForReport`, `expenseTypeFilters`, `receiptTypes`,
`paymentTypes`, `currencies`, `countries`, `siteSettings`, `userPermissions`,
`attendeeSettings`, `attendeesMetaData`, `mruAttendees`, `processingReceipts`,
`combineRoomRateAndTaxes`, `reportExceptions`, `entryExceptions`

쓰기: **`updateExpense`**, `createExpense`, **`createSameRoomRateItemizations`**,
`CDS_addRecentExpenseTypes`, `CDS_addRecentLocation`, `CDS_addMultipleRecentLists`

검색: **`attendeesByPredictiveSearch`**

## 경비 목록 한 행 (`reportEntriesDetails.entries[]`)

```
expenseId, summary {
  id, transactionDate(date-iso), transactionAmount{value:int, currencyCode},
  postedAmount, approvedAmount, claimedAmount,
  vendor{description}, expenseType{id, code, name},
  attendeeCount:int, parentExpenseId, receiptImageId, eReceiptImageId, rpeKey,
  isImageRequired, isPaperReceiptRequired, isPersonalExpense,
  meta { hasReceiptImage, hasItemizations, hasAttendees, hasExceptions,
         hasBlockingExceptions, hasComments, hasAllocation, canDelete,
         hasSourceCreditCard, hasSourceEReceipt, hasSourceExpenseIt, ... }
}
```

이 한 응답이 `concur_rows.READ_ROWS_JS`(90줄 JS)와 그 뒤의 것들을 대신한다.

- 행 탐색·셀 위치 추정 → 배열 순회
- 금액을 스크린리더 요약에서 긁던 것 → `transactionAmount.value`
- `aria-rowcount` vs 보이는 행 수 경쟁, `경비 ID 누락/중복` → 응답은 다 왔거나 안 왔거나
- 영수증 유무 판정(`rcpt-btn-attach-receipt` vs `receipt-thumbnail-button-*` 오판) →
  `meta.hasReceiptImage` + `receiptImageId`
- 명세 자식 행 구분 → `parentExpenseId`

## 저장은 희소 업데이트다 (`updateExpense`)

실측된 두 번의 `fields`:

```
{ expenseTypeId, receiptTypeId, custom14{listItemId, value} }   # 유형 변경
{ businessPurpose, comment }                                     # 목적·코멘트
```

**보낸 필드만 바뀐다.** 저장소의 "빈칸은 Concur의 현재 값을 유지한다는 뜻" 정책이
API에 그대로 있다. 같은 variables에 `attendees`, `taxFields`가 최상위로 들어가고
`shouldCopyDownFields`, `updateRecentExpenseType` 같은 스위치가 붙는다.

## 응답이 곧 검증이다

`updateExpense` 응답이 `entry` 와 `entryExceptions` 를 **같이** 준다. 별도로
`reportExceptions.entryExceptions[]` 도 있다. 즉 "저장했는가"와 "Concur가 퇴짜를
놨는가"가 한 응답에 있다. 지금처럼 저장 누른 뒤 화면을 다시 읽어 추측할 필요가 없다.
`날짜 범위 닫기`·`유효한 정보를 제공해야 합니다` 류가 구조화된 예외로 온다.

## 숙박 명세

`createSameRoomRateItemizations` — **"가격 동일"이 작전 이름에 박혀 있다.** 응답은
`entry.itemizations[]` + `itemizationsExceptions[]`. `combineRoomRateAndTaxes` 조회도
따로 있다. 콤보박스·1박 예외·숨은 저장 버튼·패널 클릭 가로채기가 전부 사라지는 자리다.

## 참석자

`attendeesByPredictiveSearch` → `attendees[]`:

```
atnKey, id, atnTypeKey, atnTypeId, atnTypeName, atnTypeCode,
firstName, lastName, mi, preferredName, emailAddress,
company, title, suffix, isFavorite, versionNumber
```

- **`emailAddress` 가 필드로 온다.** 지금은 `kyungsik.oh@siemens.com` 에서 도메인을
  떼고 화면 이름과 토막 대조(`name_matches`)를 하는데, 여기서는 메일 주소로 정확히
  대조할 수 있다. 토막 대조가 필요 없어진다.
- `isFavorite` — `attendee_favorites.json` 이 흉내내던 것이 원래 있다.
- **`versionNumber` 가 있다.** 낙관적 동시성 제어다. "참석자가 통째로 지워진" 사고
  (실제로 한 번 냈다)를 구조적으로 막을 수 있는 수단이다.

## 영수증 첨부

```
POST /spend-graphql/upload   FormData{file}  -> 202 {imageId, href, id}
```

그 다음 `imageId` 를 엔트리에 붙이는 mutation(1회차에서 이름 미확정). 깨끗한 두 단계다.
"영수증이 제일 안 깨끗할 것"이라던 예상은 틀렸다.

## 좋은 소식 둘

1. **persisted query가 아니다.** 질의문을 통째로 보낸다(`persistedQuery` 0건,
   `sha256Hash` 0건). 해시만 보내면 같은 호출을 만들기가 훨씬 어려웠다.
2. GraphQL은 스스로를 설명한다. `operationName` + 타입 붙은 질의문이 호출마다 붙어
   다니고, 변경은 `mutation` 으로 명시되고, 오류가 구조화돼서 온다.

## 작전 이름 전체 (2회차 실측, 2026-11-05)

C단계가 하는 일에 **전부 대응되는 작전이 있다.**

| C단계가 하는 일 | 지금 (DOM) | GraphQL 작전 |
|---|---|---|
| 경비 목록 읽기 | `READ_ROWS_JS` 90줄 | `GetExistingExpenseEntries`, `GetReportExceptionsAndEntries` |
| 경비 하나 상세 | `concur_detail.DETAIL_JS` | `GetExistingExpenseEntry` (5회) |
| 목적·코멘트·유형 저장 | 저장 버튼 찾기·클릭 | **`UpdateExistingExpenseEntry`** |
| 참석자 검색 | 콤보박스 + 토막 이름 대조 | `GetAttendeesByPredictiveSearch`, `GetMRUAttendees` |
| 참석자 저장 | 추가 버튼·저장 (삭제 사고 낸 자리) | **`SaveExpenseAttendees`** |
| 숙박 명세 | 콤보박스·1박 예외·숨은 저장버튼 | `GetNewItemizationForm` → **`CreateSameRoomRateMutation`** |
| 영수증 첨부 | `set_input_files` + 저장 | `upload`(202) → **`AttachImage`** |
| 영수증 붙었나 확인 | 썸네일 버튼 탐색 | `GetLineItemImage`, `GetLineItemImages` |
| 예외(퇴짜) 확인 | 안내창 글자 읽기 | `GetExistingEntryExceptions` |

곁다리로 나온 것: `SaveNewExpenseEntry`(경비 생성 — 2회차에서 사람이 수동 생성),
`MatchAvailableExpensesToReport`·`GetAvailableExpenses`·`FindMatchingAvailableExpenses`
(카드 피드 경비를 리포트에 붙이는 경로), `GetAvailableReceipts`·`GetProcessingReceipts`
(영수증 풀), 그리고 폼·목록 조회들: `GetExpenseFormExpenseTypes`, `GetExpenseTypesForReport`,
`GetRecentExpenseTypes`, `GetReceiptTypes`, `GetPaymentTypes`, `GetCurrencies`,
`GetCountries`, `GetSiteSettings`, `GetUserPermissions`, `GetGroupSettingsForReceipts`,
`GetReportPageData`, `GetReportPageSecondaryData`, `GetReportIdAndKey`,
`GetRecentLists`, `GetRecentLocations`, `AddMultipleRecentLists`.

### 구형 .NET 라우터는 우리 경로가 아닐 것이다

`expenseRouter.ashx` 17회는 2회차에서도 이름이 안 붙었다(그 기록은 `action`/`method`
를 남기기 전 코드로 받았다). 다만 **위 표의 아홉 가지가 전부 GraphQL 로 덮인다** —
그래서 이 17회는 리포트 화면 껍데기나 여행(Travel) 쪽일 가능성이 높다. 같은 회차에
`services/travel/savelog.asp` 7회가 함께 나온 것도 그 방향이다. 확정은 아니다.

### 2회차에서 관측된 실패

`GetNewItemizationExpenseTypesForm` 1회 `network-failed`.
`nui/expense/nui-shell/api/logs` 500 5회 (Concur 자기 로그 전송, 우리 것 아님).

## 아직 모르는 것

- **구형 `expenseRouter.ashx` 17회가 무엇을 하는가.** `{action, method, data}` 꼴
  (Ext.Direct). 1·2회차 모두 이름을 못 받았다 — 그 기록을 받은 코드가 아직 그 자리를
  가리고 있었다. 위 표가 다 덮이므로 우리 경로가 아닐 가능성이 높지만, 확인은 남았다.
- ~~영수증 `imageId` 를 엔트리에 붙이는 mutation 이름~~ → `AttachImage` (2회차).
- 1회차는 사람이 **수동 생성한** 숙박 경비로 명세를 시험했다. 카드 피드로 들어온
  경비는 `createExpense` 가 아니라 기존 엔트리 수정 경로를 탄다 — 실제 경로는
  카드 피드 숙박 건으로 다시 봐야 한다.
- 스키마 introspection 이 열려 있는지.
- 약관: 자기 세션 자동화가 사내에서 허용되는지는 **사람이 관리자에게 확인할 일**이다.

## 그래서 순서

1. **읽기부터.** `reportEntriesDetails` 로 목록을 읽고, 한동안 `READ_ROWS_JS` 와
   **둘 다 돌려 비교**한다. 어긋나면 DOM을 믿고 기록한다. 부수효과가 없어 위험이 0이다.
2. 읽기가 계속 일치한 뒤에 **쓰기**를 필드 종류별로 하나씩. `updateExpense` 의
   `fields` 가 희소 업데이트라 지금 정책과 그대로 맞는다.
3. **DOM 경로는 지우지 않는다.** 죽은 코드가 아니라 응답 모양이 예상과 다를 때의
   폴백이다. 문서 없는 의존을 버티게 하는 건 그것 하나다.
4. 지금의 저장 후 읽기 재확인은 **남긴다.** 응답은 "저장됐다"를 말하고, 재확인은
   "맞는 걸 저장했다"를 말한다. 둘은 다른 질문이고, 엉뚱한 경비를 건드리면 감사 문제다.
5. 1단계에서 **생성·삭제는 HTTP로 하지 않는다.** 기존 엔트리 수정만. 되돌릴 수 없는
   것이 거기 있다.


## 진행 기록

- **2026-11-05 1단계 푸시.** `src/concur_graphql.py`(읽기만) + `python -m src.compare_rows`.
  목록을 화면과 GraphQL로 둘 다 읽어 비교한다. 아직 **옮기지 않고 잰다.**
  - 질의문은 실측에서 받은 것을 쓴다. 지어낸 것이 아니다.
  - 인증은 `ctx.request` 가 브라우저 컨텍스트 쿠키를 공유하는 것으로 끝난다.
    실측에서 이 호출에 `authorization` 헤더가 없었다. 그 가정은 실제 Chromium으로
    검증한다(`tests/test_graphql_session.py::test_브라우저_쿠키로_나간다`).
  - 출발점은 `GetReportIdAndKey` 다. `userId`/`contextRole` 이 nullable 이고
    `employee` 가 자기 `userId` 를 돌려주므로 reportId 하나로 시작한다.
  - **모르는 것을 추측으로 메우지 않는다.** ISO가 아닌 날짜, KRW가 아닌 통화,
    소수 금액은 값을 비우고 사유를 남긴다. 응답 모양이 다르면 예외다.
  - 명세 자식 행(`parentExpenseId` 있음)은 비교에서 뺀다. 화면 목록에는 부모만 나온다.
  - mutation 을 부르지 않는 것을 테스트로 강제한다.
  - 다음: 실측 비교가 한동안 0건이면 목록 읽기를 GraphQL로 바꾸고 화면 경로를
    폴백으로 남긴다. 쓰기는 그 뒤, 그리고 약관 답을 받은 뒤.

- **2026-11-05 실측 1회차 비교: 차이 0건.** 10월 리포트(`02FD45A768AB43F686BF`), 화면
  19행 · GraphQL 19행, 양쪽 못 읽은 값 0행, 날짜·금액·영수증 유무 전부 일치.
  주소창의 리포트 ID가 GraphQL의 `reportId` 였고(`rptKey` 아님), `contextRole=TRAVELER`,
  `userId` 를 null로 넘겨 받아오는 출발점이 실제로 동작했다.
  남은 미검증: 명세 자식 행(이 리포트엔 0건), 화면이 날짜를 못 읽는 경우.

- **2026-11-05 2단계 푸시: 교차검증을 켰다.** 대체가 아니다.
  - **경비는 한 달에 한 번 쓴다.** "표본을 쌓아 판단" 하면 몇 달짜리가 되고, 그동안
    청산·주문 경로가 미검증으로 남는다. 그래서 지금 붙이되 **더 보수적으로** 붙였다.
  - 빈 자리만 채우고, 어긋나면 **어느 쪽도 고르지 않고 비운다** → 기존 대상별 보류가
    그 거래만 붙잡는다. 화면 혼자 결정하던 이전보다 보수적이다.
  - **빈 자리가 있을 때만 묻지 않는다.** 그러면 값이 다 찬 행의 어긋남을 놓치고
    교차검증이라는 목적이 없어진다. 비용은 리포트별 20초 캐시가 막는다.
  - 캐시 TTL(20초)은 `ready_report` 의 '3회 연속 같은 결과'(500ms×3=1.5초)보다 훨씬
    길어야 한다. 짧으면 폴링 중에 결과가 바뀌어 목록이 영원히 안정되지 않는다.
  - `fill_gaps` 는 **어떤 예외도 올려보내지 않는다.** 모든 접근을 다 감쌌겠지로
    믿지 않고 바깥에서 한 번 막는다 - 보완이 본 작업을 막으면 되던 것이 안 된다.
  - 끄는 스위치: `AUTO_CONCUR_NO_GRAPHQL=1`.
  - 쓰기는 여전히 없다. mutation 을 부르지 않는다.

- **2026-11-05 3단계 푸시: 저장·첨부를 GraphQL로.** 목적·코멘트 저장과 영수증 첨부만.
  - **질의문을 직접 썼다.** `UpdateExistingExpenseEntry` 기록이 정확히 4000자에서
    잘려서(`gqlDoc` 상한) 재생할 수 없었다. 관측된 필드 이름만으로 최소 문서를
    만들었고, 원본이 끌어오는 거대 프래그먼트와 `CDS_addRecentExpenseTypes`
    (policyId 필요)는 덜어냈다. 보강 근거: DOM 셀렉터가 `#businessPurpose` /
    `textarea#comment` 이고 GraphQL `fields` 도 같은 이름이다.
  - **보내기 전 검증.** `@skip(if: $skip)` 로 같은 문서·같은 변수를 보내면 문서와
    변수는 검증되고 resolver 는 실행되지 않는다. 내 문서가 틀렸으면 아무것도
    건드리기 전에 안다.
  - **NotSent / MaybeSent.** 보내기 전에 막힌 것만 화면 경로로 넘어간다. 보낸 뒤
    결과를 모르면 재시도도 폴백도 하지 않는다 - 둘 다 이중 저장이 된다.
    업로드 실패는 NotSent 다(업로드는 경비를 건드리지 않는다).
  - 저장 응답의 `entryExceptions` 를 읽어 차단 예외·메시지·빈 필드 이름을 보고한다.
  - 저장 뒤 목록 캐시를 버린다(`forget`). 묵은 값으로 재확인하지 않는다.
  - **화면에 남긴 것:** 유형 변경(policyId 미확보), 참석자(versionNumber 필요),
    숙박 명세(roomRateExpenseTypeId 필요). 참석자는 전에 실제로 데이터를 날린
    자리라 더 신중해야 한다.
  - 다음에 필요한 것: policyId 를 주는 작전, `GetAttendeesByPredictiveSearch` 의
    versionNumber 를 들고 `SaveExpenseAttendees` 로 가는 경로.

- **2026-11-05 4단계 푸시: 유형 변경 이관 + 스키마 조회 도구.**
  - **유형 변경은 `policyId` 가 필요 없었다.** 그걸 쓰던 `CDS_addRecentExpenseTypes`
    를 우리 최소 문서에서 이미 뺐기 때문이다. `fields.expenseTypeId` 하나면 된다.
    저장소의 코드(`PARKG`·`01143`·`LODNG`)가 실측 `expenseTypeId` 모양(digits /
    1-8자)과 같다. 저장 뒤 `verify_edit` 가 화면에서 유형을 다시 읽어 대조하므로
    독립 확인이 남는다.
  - **남은 둘의 구멍이 무엇인지 확정했다.**
    - 참석자: `GetAttendeesByPredictiveSearch` 가 `atnKey`·`atnTypeCode`·
      `versionNumber` 를 주고 `SaveExpenseAttendees` 가 그걸 받는다. 경로는 다 있다.
      구멍은 참석자별 `transactionAmount`·`instanceCount` 다 - 화면이 보낸 것은
      봤지만 **필수인지, 값을 어떻게 나누는지** 모른다. 그리고 `saveAttendeesLegacy`
      는 목록을 통째로 교체한다 - 전에 참석자가 다 지워진 사고가 이 성질이다.
    - 숙박 명세: `roomRateExpenseTypeId` 의 출처인
      `GetNewItemizationExpenseTypesForm` 응답이 2회차에서 `network-failed` 라 비었다.
  - **`python -m src.inspect_schema` 를 만들었다.** 두 구멍 다 "어떤 필드가 필수인가"
    여서, 화면이 보낸 것을 더 모으는 것보다 스키마에 직접 묻는 쪽이 확정적이다.
    읽기 전용이고 mutation 을 부르지 않는다. introspection 이 꺼져 있으면 한 번
    돌려서 그걸 알게 된다.

- **2026-11-05 introspection 은 막혀 있다(확정).** `__type` 조회가 전부 HTTP 400 에
  `{"message":"An error occurred","extensions":{}}` — 일부러 뭉갠 운영용 메시지다.
  원인을 가르려고 같은 내용을 Concur 가 아는 작전 이름(`GetSiteSettings`)으로 다시
  보냈는데 같은 400 이었다. **즉 거부 대상은 작전 이름이 아니라 `__type` 이다.**
  (첫 시도는 `operationName` 만 바꾸고 문서 안의 `query AutoConcurType` 을 그대로
  둬서 실험이 성립하지 않았다 - GraphQL 에서 그건 "그런 작전 없음" 이라 정책과
  무관하게 400 이다. 문서 안까지 바꿔야 한다.)
  덤으로 확인된 것: **서버는 아는 작전 이름만 받는 게 아니다.** 우리 쓰기 경로가
  실제 이름에 줄인 문서를 싣는 방식이라 그 점이 중요했다.

- **2026-11-05 5단계 푸시: `python -m src.probe_fields`.** 스키마를 못 물으니
  시험한다. `@skip(if: true)` 는 **변수 검사는 하고 resolver 는 실행하지 않는다** -
  필드를 하나씩 빼서 보내면 어느 것이 필수인지 드러나고 아무것도 쓰이지 않는다.
  ID 는 가짜를 쓴다(검증은 타입과 존재만 보고, 어차피 실행되지 않는다).
  시험 7건: 참석자 다섯 필드 그대로 / `transactionAmount` 빼고 / `instanceCount`
  빼고 / `versionNumber` 빼고 / 금액·수량 둘 다 빼고, 숙박 명세
  `roomRateExpenseTypeId` 주고 / 빼고.
  **안전 성질을 테스트로 묶었다** - 실제 실행 경로에서 나가는 모든 호출의
  `skip` 이 `True` 인지 본다. 하나라도 빠지면 실제 저장이 일어난다.

- **2026-11-05 시험 1회차: 7건 전부 통과. 그런데 해석할 수 없다.**
  참석자의 `versionNumber` 까지 빼도 통과했다. 가능성이 둘인데 가릴 수가 없다 -
  정말 전부 선택이거나, **이 서버가 `@skip` 붙은 호출을 아예 검사하지 않거나.**
  **대조군을 안 넣은 것이 설계 잘못이다.** 반드시 거부돼야 하는 것을 같이 보내지
  않으면 "전부 통과" 는 정보가 0이다.
  대조군 4건 추가: 없는 입력 필드(`atnKeyNOPE`), 없는 mutation 필드
  (`saveAttendeesLegacyNOPE`), 숫자 자리에 글자, 필수 변수 통째로 누락.
  이게 전부 거부돼야 시험 결과가 뜻을 갖는다. 하나라도 통과하면 그렇게 말한다.

- **2026-11-05 시험 2회차(대조군 포함): 결과가 뜻을 갖는다.**
  대조군 4건 전부 거부, 시험 7건 전부 통과. 두 가지가 확정됐다.
  1. **`@skip(if: true)` 사전검증이 진짜로 검사한다.** 저장·첨부에 붙여둔 안전장치가
     실제로 작동한다는 뜻이다 - 이게 확인 안 됐으면 그 장치는 장식이었다.
  2. 참석자의 `versionNumber`·`transactionAmount`·`instanceCount` 와 명세의
     `roomRateExpenseTypeId` 가 **전부 선택**이다.
  다만 "검증을 지난다" 와 "업무 규칙에 맞다" 는 다르다. 예를 들어
  `transactionAmount` 를 안 보냈을 때 Concur 가 알아서 나누는지 0으로 두는지는
  이걸로 알 수 없다.

- **2026-11-05 숙박 `fields` 키가 드러났다.** `SaveNewExpenseEntry` 의 실제 요청에
  `fields: {expenseTypeId, hotelCheckinDate, hotelCheckoutDate, transactionDate,
  vendorName, locationId, paymentTypeId, custom10{listItemId, value}, ...}` 가 있다.
  즉 **숙박 날짜는 `updateExpense` 의 `fields` 로 넣을 수 있다** - 달력 위젯도
  날짜 범위 입력칸도 필요 없다. 다만 숙박위치는 `locationId`(hex), Booking Channel 은
  `custom10{listItemId, value}`(hex) 라 **이름→ID 조회가 따로 필요하다.**

- **2026-11-05 기록기 상한이 진짜 병목이었다.** 질의문을 4000자에서 자르고 있었는데,
  GraphQL 문서는 **프래그먼트가 앞에 오고 작전 정의가 뒤에 와서** 자르면 정작 필요한
  작전 서명이 날아간다. 우리가 쓸 문서 둘이 정확히 거기 걸렸다:
  `GetAttendeesByPredictiveSearch`(프래그먼트가 4000자를 다 먹음),
  `CreateSameRoomRateMutation`. `UpdateExistingExpenseEntry` 도 그래서 직접 썼다.
  상한을 20000 으로 올리고, 잘리면 `/* …잘림 N자 */` 를 적는다 - 조용히 자르면
  그게 Concur 탓인 줄 안다.

- **2026-11-05 3회차 기록: 잘렸던 문서 둘이 온전히 들어왔다.**
  `GetAttendeesByPredictiveSearch` 2547자, `CreateSameRoomRateMutation` 10599자.
  상한을 올린 것이 맞았다. 그리고 구형 라우터 이름도 드디어 나왔다 -
  `Attendee.GetAttendeeTypes`, `Attendee.GetAttendeeData`,
  `FormsAndFields.GetFormFields`, `FormsAndFields.GetAttendeeListFieldsForEntry`.
  **구형 .NET 라우터는 참석자 메타데이터 경로였다.** "우리 경로가 아닐 것" 이라던
  추측은 틀렸다.

- **2026-11-05 6단계 푸시: 숙박 항목별 명세를 GraphQL로.**
  `createSameRoomRateItemizations(reportId, expenseId, userId, contextRole,
  roomRateObject: {roomRate})`. `roomRateExpenseTypeId` 는 뺐다 - 원본 문서에선
  `String!` 이지만 필드 인자 자체는 선택임을 시험으로 확인했다.
  **세 조건을 다 만족할 때만 쓴다.** 1박 요금이 전부 같을 것('가격 동일'), 이미
  명세가 없을 것, 그리고 **있는지 모르면 만들지 않을 것** - 이 mutation 은
  명세를 '만들어서' 중복이 되돌리기 어렵다. 판단 근거는 목록 질의의
  `meta.hasItemizations` 다(`Entry.has_itemizations`).
  명세 전에 상세를 저장한다. 날짜 범위·위치·채널을 화면에서 넣어둔 상태라
  저장하지 않으면 그 값이 날아간다.
  이 한 호출이 콤보박스·1박 예외·숨은 저장 버튼·패널 클릭 가로채기를 대체한다.

- **참석자는 아직 못 간다.** 검색 서명이 드러났지만
  `attendeesByPredictiveSearch(searchTerms, includeInactiveEmp,
  includeAtnTypeKeys: [Int!]!, excludeKeys: [String!]!)` 에서
  **`includeAtnTypeKeys` 값의 출처**가 남는다. 실측에서 정수 2개가 갔는데 그 값이
  어디서 오는지는 구형 라우터의 `Attendee.GetAttendeeTypes` 로 보이고, 그
  요청 `data` 모양은 아직 모른다. 빈 배열로 보내면 검증은 지나지만 결과가
  비어 올 수 있다 - 참석자는 전에 실제로 데이터를 날린 자리라 그렇게 넘기지 않는다.

- **1박 요금이 다른 경우가 흔하다(사용자 지적).** 20만원 3박이면
  `nightly_split` 이 `[66667, 66667, 66666]` 을 준다 - 합은 정확히 20만원이지만
  요금이 같지 않다. `createSameRoomRateItemizations` 는 **'가격 동일' 전용**이라
  66667 을 보내면 3박 합이 200,001원이 되어 경비 총액을 넘긴다. 즉 쓰면 안 되는
  호출이고, 막아둔 조건이 맞았다. 다만 조용히 넘어가면 "이관이 안 되네" 로
  보이므로 사유를 화면에 적는다.
  **남은 것: '가격 다름' 쪽 작전.** 세 번의 기록 모두 `CreateSameRoomRateMutation`
  뿐이고(사용자가 '가격 동일' 로만 해봤다), 요금이 제각각인 명세를 만드는 작전은
  아직 본 적이 없다. 그것 하나면 숙박이 완전히 넘어간다.

- **2026-11-05 4회차 기록: 두 구멍이 다 메워졌다.**
  - **`CreateDifferentRoomRateMutation`** (10612자, 온전). `SameRoomRate` 와 다른
    점은 `roomRateObject` 가 **리스트**라는 것 하나다:
    `[RoomRateAndTax!]!` ← `[{roomRate: 66667}, {roomRate: 66667}, {roomRate: 66666}]`.
    `nightly_split` 결과를 그대로 넘기면 합이 경비 총액과 정확히 맞는다.
  - **구형 라우터 이름이 전부 나왔다.** `Attendee.GetAttendeeTypes`,
    `Attendee.GetAttendeesForProdCode`, `Attendee.GetPermitAttendeeImport`,
    `FormsAndFields.GetFormFields`, `FormsAndFields.GetListInfo`,
    `FormsAndFields.GetFormLists`, `FormsAndFields.GetAttendeeListFieldsForEntry`,
    `Country.GetCountriesList`. 전부 참석자·폼 메타데이터다.
  - `Attendee.GetAttendeeTypes` 의 요청은
    `{action: "Attendee", method: "GetAttendeeTypes", data: {forEmpKey}}` 이고
    응답은 `{attendee_GetAttendeeTypes: {records: [{atnTypeKey, atnTypeCode, name,
    isIncluded, ...}], recordCount}}` 다. **`includeAtnTypeKeys` 의 출처가 이것이다.**
  - 이 회차에는 사용자가 숙박 날짜를 바꾸고 명세를 한 번 지웠다
    (`DeleteExpenseEntries`). 삭제는 이관 대상이 아니다.

- **2026-11-05 7단계 푸시: 요금이 달라도 명세를 GraphQL로.**
  `gq.itemize(page, expense_id, nightly)` 하나로 갈린다 - `len(set(nightly)) == 1`
  이면 Same, 아니면 Different. 20만원 3박 같은 흔한 경우가 이제 화면으로 안 떨어진다.

- **참석자에 남은 것.** 경로는 이제 다 보인다:
  `Attendee.GetAttendeeTypes`(구형 라우터) → `atnTypeKey` 목록 →
  `attendeesByPredictiveSearch(searchTerms, includeInactiveEmp, includeAtnTypeKeys,
  excludeKeys)` → `SaveExpenseAttendees`.
  남은 불확실: 구형 라우터가 보내는 커스텀 헤더(`camelcase`, `fixjson`, `x-nui`)의
  값을 모른다. 응답 모양을 바꾸는 스위치로 보여서, 안 보내면 키가 다르게 올 수 있다.
  `forEmpKey` 가 GraphQL 의 `userId` 와 같은 값인지도 미확인(둘 다 len33-128).
  참석자는 전에 실제로 데이터를 날린 자리라 그 둘을 추측으로 넘기지 않는다.

- **남은 셋이 한 곳에서 막혀 있다: 구형 .NET 라우터의 헤더 값.**
  `expenseRouter.ashx` 가 `camelcase`, `fixjson`, `x-nui` 를 보내는데 **응답 모양을
  바꾸는 스위치**로 보인다. 값을 모르면 같은 호출을 만들 수 없다. 그 라우터가
  막고 있는 것:
  - 참석자 (`Attendee.GetAttendeeTypes` → `includeAtnTypeKeys`)
  - Booking channel (`FormsAndFields.GetFormLists` → 이름→키)
  - (숙박위치는 `GetRecentLocations` 로 일부 되지만 최근 목록 6개뿐이다)
  그래서 기록기가 **허용 목록에 든 헤더만 값까지** 남기게 했다. 프로토콜
  스위치뿐이고 `cookie`/`authorization` 류는 이름만 남는다 - 테스트로 확인한다.

- **마일리지도 이관 대상에 들어왔다(사용자 결정).** 다만 `SaveNewExpenseEntry` 는
  4000자에 잘린 것 하나뿐이고 그나마 숙박 건이라, 마일리지 고유 필드(차량·거리·
  출발지·도착지)의 `fields` 키는 아직 본 적이 없다. 마일리지 생성을 한 번 기록해야
  한다. **생성은 고치기와 다르다** - 틀리면 없던 재무 기록이 생기므로 `@skip`
  사전검증과 구조화된 예외 보고를 그대로 붙인다.
  (참고: 화면 경로도 중간에 실패하면 반쯤 채운 새 경비가 남는다. mutation 한 번은
  그 반쯤 상태가 없다.)

- **2026-11-05 5회차 기록: 구형 라우터 헤더 값이 나왔다.**
  `{camelcase: "Y", fixjson: "Y", x-nui: "Y", content-type: "application/json"}`.
  이것 하나가 참석자를 막고 있었다.
  (사용자 관찰: 숙박위치·Booking channel 드롭다운을 만질 때 화면에 아무 변화가
  없었다. 실제로는 `FormsAndFields.GetFormLists` 가 2회 나갔는데, 드롭다운을 열 때가
  아니라 경비 상세가 뜰 때 미리 받아두는 것으로 보인다.)

- **2026-11-05 8단계 푸시: 참석자를 GraphQL로.** 세 걸음이다.
  `Attendee.GetAttendeeTypes`(구형 라우터, `{forEmpKey}`) → `atnTypeKey` 목록 →
  `attendeesByPredictiveSearch(searchTerms, includeInactiveEmp, includeAtnTypeKeys,
  excludeKeys)` → `SaveExpenseAttendees`.
  - **메일 주소로 정확히 맞춘다.** 검색 결과가 `emailAddress` 를 주므로 계정 이름
    대조가 된다. `name_matches` 토막 대조는 못 맞출 때의 폴백으로만 남는다.
  - **`versionNumber` 를 같이 보낸다.** 낙관적 동시성 - 남이 먼저 바꿨으면 덮지 않는다.
  - **한 명이라도 유일하게 못 고르면 아무도 보내지 않는다.** `saveAttendeesLegacy` 는
    목록을 통째로 교체해서, 반쯤 고른 목록을 보내면 나머지가 지워진다. 전에 실제로
    낸 사고가 그 모양이었다.
  - 금액은 `nightly_split(경비금액, 참석자수)` 로 나눈다 - 합이 경비 금액과 정확히
    맞는다. 화면이 보내던 것과 같은 자리이고, 저장소의 기존 반올림 규칙을 쓴다.
  - `forEmpKey` 가 `userId` 와 같은 값이라고 **추정**했다. 틀리면 records 가 비고,
    그때는 멈춰서 화면 경로로 간다 - 빈 목록으로 검색하지 않는다.

- **마일리지 생성 명세가 드러났다(아직 안 옮김).** `SaveNewExpenseEntry` 8253자 온전.
  `createExpense(dataContext: {reportId, fields, taxFields, attendees}, userContext)`.
  마일리지 `fields` 는 `expenseTypeId, transactionDate, countryCode, startLocation,
  endLocation, taxRateLocation, paymentTypeId, exchangeRate{operation, value},
  receiptTypeId, comment, custom13{value}, custom14{...}, vehicleId(digits),
  odometerStart/End, personalDistance, totalDistance, passengerCount,
  has*(불리언 9개), transactionAmount{value, currencyCode}, receiptImageId` 다.
  **안 옮긴 이유:** 저장소가 지금 유형·차량·결제수단을 **화면의 표시 이름**으로
  고른다. GraphQL 로 가려면 이름→코드 조회가 넷(경비유형, 차량, 결제수단, 국가)
  필요하다. 그리고 `transactionAmount` - 마일리지 금액은 거리 x 정책 요율이라
  우리가 계산해 보내면 정책 요율을 덮을 수 있다. 빼고 보내서 Concur 가 계산하는지
  응답(`entry.transactionAmount`)으로 확인하는 길이 있지만, 아직 안 했다.

- **2026-11-05 마일리지 조회 설계: 넷이 아니라 둘이면 된다.**
  화면이 하는 방식을 따라가면 된다. `GetNewExpenseEntry(reportId, expenseTypeId,
  userId, contextRole)` 가 **`mainForm.fields` 42칸을 defaults 와 함께** 준다
  (`id, label, value{listValue}, defaultValue, isRequired, requiredForSave, list,
  control, dataType`). 즉 `countryCode`·`paymentTypeId`·`receiptTypeId`·
  `exchangeRate`·`custom13` 을 따로 조회할 필요 없이 **폼의 기본값을 그대로 쓰고
  우리 값(차량·날짜·출발/도착지·거리)만 덮으면 된다.**
  남는 조회는 둘이다: 경비유형 이름→코드(`GetExpenseTypesForReport` 가
  `{id, code, name, text}` 를 준다), 그리고 **차량 이름→`vehicleId`**.

- **차량 목록의 출처를 아직 못 봤다. 원인은 내 기록기였다.**
  배열을 `[0]` 한 칸만 기록해서, 42칸짜리 폼에서 차량 칸이 거기 있어도 안 보였다.
  `FormsAndFields.GetFormLists` 가 여러 목록을 돌려주는 것도 첫 키만 보였다.
  → 앞쪽 60칸의 모양을 **합쳐서** 기록하도록 고쳤다(`[0]` 대신 `[*]`). 키는
  합집합이고 값이 다르면 `null|str:len1-8` 처럼 둘 다 적는다. 값은 여전히 안 남는다.
  예상 경로: `mainForm.fields` 에서 `label` 이 차량인 칸 → 그 칸의 `list` id →
  `FormsAndFields.GetFormLists([listId])` → `records[{key, code, shortcode, txt}]`
  에서 `txt` 로 맞추고 `key` 를 `vehicleId` 로 쓴다. 다음 기록으로 확인한다.

- **2026-11-05 6회차: 폼 칸의 모양이 합쳐져 보인다.** `mainForm.fields` 42칸에
  `id`, `label`, `control`, `dataType`, `isRequired`, `requiredForSave`,
  `value{listValue, dateValue, stringValue, integerValue, amountValue, booleanValue,
  operation}`, `defaultValue{value, code, listItemId}`, `options`,
  `list{id, level, parentId, displayFormat, ...}` 가 있다.
  **차량 목록은 그 중 `list` / `options` 안에 있을 텐데 깊이 6에서 잘렸다.**

- **기록기 상한을 전부 올렸다(세 번째이자 마지막이길).** 좁게 잡아서 세 번
  막혔다: 질의문 4000자, 배열 첫 칸, 깊이 6. 막힐 때마다 그게 Concur 쪽 제약인
  줄 알고 한 번씩 헛걸음했다. 이제 DEPTH 12, KEYS 120, SAMPLE 60, DOC 20000 이다.
  **모양만 남기므로 상한이 커져도 값은 안 샌다** - 깊은 구조에서도 날짜가
  `str:date-iso` 로만 남는 것을 테스트로 확인했다. 그래도 걸리면 `'...'` 대신
  `(깊이 12 초과)` 라고 적어서, 다음에는 그게 내 상한인 줄 바로 알 수 있다.

- **2026-11-05 7회차: 차량 옵션이 폼 안에 그대로 있었다.** 깊이를 올리니
  `mainForm.fields[].options` 가 `[{id: "str:digits", code, value: "표시 이름"}]` 로
  보인다. `vehicleId` 가 digits 였으니 **그게 그대로 차량 ID 다.**
  즉 `FormsAndFields.GetFormLists` 로 목록을 따로 받아올 필요가 없다 -
  조회 넷이 필요하다던 처음 판단도, 둘이면 된다던 그 다음 판단도 과했다.
  폼 칸에는 `list{id, level, parentId, displayFormat}`, `value{listValue, dateValue,
  stringValue, integerValue, booleanValue, operation, value, amountValue}`,
  `defaultValue{value, code, listItemId}`, `isRequired`, `requiredForSave` 가 있다.

- **2026-11-05 9단계 푸시: 마일리지 생성을 GraphQL로.** 이 도구가 하는 **유일한
  생성**이다. 그래서 고치기보다 더 조인다.
  1. `GetExpenseTypesForReport` 로 마일리지 유형 이름 → id (유일할 때만)
  2. `GetNewExpenseEntry(그 id)` 로 폼 42칸
  3. 폼의 `requiredForSave` 칸은 폼이 들고 있는 값을 그대로 쓰고, 우리 값만 덮는다
  4. `SaveNewExpenseEntry`
  - **하나라도 값을 못 만들면 아무것도 보내지 않는다.** 반쯤 만든 경비는 사람이
    찾아 지워야 한다.
  - **금액(`transactionAmount`)을 보내지 않는다.** 거리 x 정책 요율이라 우리가
    계산하면 회사 요율을 덮어쓴다. 응답의 `entry.transactionAmount` 로 계산됐는지
    보고, 0이면 그대로 알린다.
  - `MaybeSent` 면 화면으로 또 만들지 않는다 - 경비가 둘이 된다. 그 밖의 예외는
    전부 삼키고 화면 경로로 간다(`fill_gaps` 에서 배운 것).
  - 지도는 만든 뒤 `upload` → `AttachImage` 로 붙인다. 실패해도 경비는 제대로 생겼고
    기존 영수증 보수 경로가 받아 간다.
  - 울타리(`test_graphql_scope`)에서 `createExpense`/`SaveNewExpenseEntry` 를 금지어
    에서 풀고 허용 목록에 넣었다. 제출·삭제·승인은 그대로 금지다.
