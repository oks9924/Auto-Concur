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

## 아직 모르는 것

- **구형 `expenseRouter.ashx` 17회가 무엇을 하는가.** `{action, method, data}` 꼴
  (Ext.Direct)인데 1회차 기록이 그 이름을 가렸다. 2회차에서 나온다. 여기가 덜
  깨끗한 쪽이고, GraphQL로 안 올라온 기능이 있다면 그 부분은 DOM에 남긴다.
- 영수증 `imageId` 를 엔트리에 붙이는 mutation 이름.
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
