"""Concur의 GraphQL 계층을 사용자 세션으로 읽는다. 쓰기는 아직 없다.

## 왜

지금 C단계는 화면에 그려진 글자를 읽는다. 그래서 날짜 표시 서식, 로케일,
렌더링 타이밍이 전부 우리 문제가 된다. 한 계층 아래에서는 같은 값이 기계값으로
온다 - 2026-11-05 실측으로 확인했다(`docs/concur-api-20261105.md`).

## 권한

새 자격증명이 아니다. `browser.launch` 가 준 컨텍스트의 `request` 는 **그 컨텍스트의
쿠키를 공유한다.** 즉 사람이 SSO로 로그인한 그 세션으로 나간다. 실측에서 이 호출에
`authorization` 헤더가 없었다 - 인증이 `.concursolutions.com` 쿠키에 실려 있다는 뜻이다.

파이썬에서 부르는 쪽이 페이지 안에서 `fetch` 하는 것보다 간단하다. 엔드포인트가
`www-eu2.api...` 로 페이지와 다른 출처인데, CORS는 브라우저가 페이지 JS에 거는
제약이라 직접 HTTP 클라이언트에는 해당되지 않는다.

## 지금 단계

**읽기만 한다.** 목적은 같은 목록을 DOM과 둘 다 읽어서 비교하는 것이다. 어긋나면
DOM을 믿고 기록한다. 한동안 일치한 뒤에 쓰기를 생각한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

ENDPOINT = 'https://www-eu2.api.concursolutions.com/spend-graphql/graphql'

# 질의문은 실측에서 받은 것이다(2026-11-05). 지어낸 것이 아니다.
# 쓰지 않는 필드는 덜어냈다.

# userId/contextRole 이 nullable 이고 employee 가 자기 userId 를 돌려준다.
# 그래서 reportId 하나로 시작할 수 있다.
IDENTIFY = '''query GetReportIdAndKey($userId: String, $contextRole: ContextRoleType, $rptKey: String, $reportId: String) {
  employee(userId: $userId, contextRole: $contextRole) {
    userId
    contextRole
    expenseReport(reportId: $reportId, rptKey: $rptKey) { reportId rptKey }
  }
}'''

ENTRIES = '''query GetExistingExpenseEntries($userId: String!, $reportId: String!, $contextRole: ContextRoleType!) {
  reportEntriesDetails(userId: $userId, reportId: $reportId, contextRole: $contextRole) {
    reportId
    entries {
      expenseId
      summary {
        id
        parentExpenseId
        transactionDate
        transactionAmount { value currencyCode }
        vendor { description name }
        expenseType { id code name }
        receiptImageId
        eReceiptImageId
        isImageRequired
        meta { hasReceiptImage hasSourceCreditCard hasSourceEReceipt }
      }
    }
  }
}'''


class GraphQLError(Exception):
    """호출이 실패했거나 응답 모양이 예상과 다르다. 추측해서 메우지 않는다."""


def call(ctx, name: str, query: str, variables: dict) -> dict:
    """작전 하나를 부르고 `data` 를 준다.

    실패는 조용히 넘기지 않는다. 문서 없는 계층이라 응답 모양이 바뀌면 그걸
    알아야 하고, 모르는 모양을 추측으로 메우면 틀린 값을 쓰게 된다.
    """
    try:
        res = ctx.request.post(
            ENDPOINT,
            data={'operationName': name, 'query': query, 'variables': variables},
            headers={'content-type': 'application/json', 'accept': 'application/json'},
            timeout=30000)
    except Exception as exc:  # 네트워크·세션 문제
        raise GraphQLError(f'{name} 호출 실패: {type(exc).__name__}: {exc}') from exc
    if not res.ok:
        raise GraphQLError(f'{name} 호출이 HTTP {res.status} 를 받았습니다')
    try:
        body = res.json()
    except Exception as exc:
        raise GraphQLError(f'{name} 응답을 JSON으로 읽지 못했습니다') from exc
    errors = body.get('errors') if isinstance(body, dict) else None
    if errors:
        first = errors[0].get('message') if isinstance(errors[0], dict) else str(errors[0])
        raise GraphQLError(f'{name} 이(가) 오류를 돌려줬습니다: {first}')
    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise GraphQLError(f'{name} 응답에 data 가 없습니다')
    return data


def identify(ctx, report_id: str) -> tuple[str, str]:
    """이 세션의 userId·contextRole 을 받아온다. 리포트가 맞는지도 확인한다."""
    data = call(ctx, 'GetReportIdAndKey', IDENTIFY,
                {'userId': None, 'contextRole': None, 'rptKey': None, 'reportId': report_id})
    employee = data.get('employee') or {}
    user_id, role = employee.get('userId'), employee.get('contextRole')
    if not user_id or not role:
        raise GraphQLError('세션의 userId·contextRole 을 받지 못했습니다')
    found = (employee.get('expenseReport') or {}).get('reportId')
    if found != report_id:
        # 다른 리포트를 읽고 있으면 비교 자체가 무의미하다.
        raise GraphQLError(f'리포트가 다릅니다: 요청 {report_id} / 응답 {found}')
    return user_id, role


@dataclass
class Entry:
    """DOM의 attach_receipts.Row 와 비교할 수 있는 만큼만 담는다."""
    expense_id: str
    when: date | None
    amount: int | None
    vendor: str = ''
    expense_type: str = ''
    has_receipt: bool | None = None
    parent_expense_id: str | None = None
    raw_date: str = ''
    raw_amount: str = ''
    note: str = ''


def _as_date(value) -> tuple[date | None, str]:
    if not isinstance(value, str) or not value:
        return None, '날짜 없음'
    try:
        return date.fromisoformat(value), ''
    except ValueError:
        # ISO가 아니면 추측하지 않는다. 실측에서는 전부 ISO였다.
        return None, f'ISO 날짜가 아님: {value!r}'


def _as_amount(money) -> tuple[int | None, str]:
    """KRW 정수만 받는다. 저장소의 기존 정책과 같다."""
    if not isinstance(money, dict):
        return None, '금액 없음'
    value, code = money.get('value'), money.get('currencyCode')
    if code and code != 'KRW':
        return None, f'KRW가 아님: {code}'
    if isinstance(value, bool) or value is None:
        return None, '금액 없음'
    if isinstance(value, int):
        return value, ''
    if isinstance(value, float) and value.is_integer():
        return int(value), ''
    # 소수 원 단위는 반올림하지 않는다.
    return None, f'정수가 아님: {value!r}'


def entries(ctx, report_id: str, user_id: str, context_role: str) -> list[Entry]:
    data = call(ctx, 'GetExistingExpenseEntries', ENTRIES,
                {'userId': user_id, 'reportId': report_id, 'contextRole': context_role})
    details = data.get('reportEntriesDetails') or {}
    if details.get('reportId') != report_id:
        raise GraphQLError('목록 응답의 리포트 ID가 요청과 다릅니다')
    raw = details.get('entries')
    if not isinstance(raw, list):
        raise GraphQLError('목록 응답에 entries 배열이 없습니다')
    found = []
    for item in raw:
        summary = (item or {}).get('summary') or {}
        when, why_date = _as_date(summary.get('transactionDate'))
        amount, why_amount = _as_amount(summary.get('transactionAmount'))
        meta = summary.get('meta') or {}
        found.append(Entry(
            expense_id=item.get('expenseId') or summary.get('id') or '',
            when=when, amount=amount,
            vendor=(summary.get('vendor') or {}).get('description') or '',
            expense_type=(summary.get('expenseType') or {}).get('name') or '',
            has_receipt=meta.get('hasReceiptImage'),
            parent_expense_id=summary.get('parentExpenseId'),
            raw_date=str(summary.get('transactionDate') or ''),
            raw_amount=str((summary.get('transactionAmount') or {}).get('value', '')),
            note='; '.join(x for x in (why_date, why_amount) if x)))
    return found


def top_level(found: list[Entry]) -> list[Entry]:
    """명세 자식 행은 뺀다. 화면 목록에는 부모만 나온다."""
    return [e for e in found if not e.parent_expense_id]
