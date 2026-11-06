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
        meta { hasReceiptImage hasItemizations hasSourceCreditCard hasSourceEReceipt }
      }
    }
  }
}'''


class GraphQLError(Exception):
    """호출이 실패했거나 응답 모양이 예상과 다르다. 추측해서 메우지 않는다."""


def _why(res) -> str:
    """오류 응답의 본문을 짧게 붙인다. GraphQL 은 거기에 이유를 적는다.

    이걸 안 읽어서 한 번 헛걸음했다(2026-11-05 introspection 400). 본문은 오류
    메시지이지 거래 값이 아니므로 그대로 보여도 된다.
    """
    try:
        text = (res.text() or '').strip().replace('\n', ' ')
    except Exception:
        return ''
    return f' — {text[:300]}' if text else ''


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
        raise GraphQLError(f'{name} 호출이 HTTP {res.status} 를 받았습니다' + _why(res))
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
    report = employee.get('expenseReport')
    if not report or not report.get('reportId'):
        # 제일 있을 법한 실패다. 주소창의 ID가 GraphQL이 받는 reportId가 아니라
        # rptKey 쪽일 수 있다. 그러면 사람이 다음에 뭘 봐야 하는지 알아야 한다.
        raise GraphQLError(
            f'이 리포트 ID로 조회가 되지 않았습니다: {report_id}\n'
            '  주소창의 ID가 GraphQL이 받는 reportId가 아닐 수 있습니다(rptKey 쪽).\n'
            '  아무것도 바꾸지 않았습니다 - 이 출력을 그대로 전해 주세요.')
    found = report.get('reportId')
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
    has_itemizations: bool | None = None
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
            has_itemizations=meta.get('hasItemizations'),
            parent_expense_id=summary.get('parentExpenseId'),
            raw_date=str(summary.get('transactionDate') or ''),
            raw_amount=str((summary.get('transactionAmount') or {}).get('value', '')),
            note='; '.join(x for x in (why_date, why_amount) if x)))
    return found


def top_level(found: list[Entry]) -> list[Entry]:
    """명세 자식 행은 뺀다. 화면 목록에는 부모만 나온다."""
    return [e for e in found if not e.parent_expense_id]


# ---------------------------------------------------------------------------
# 빈 자리 보완 (2026-11-05)
#
# 화면 읽기를 대체하지 않는다. **비어 있는 자리만** 채우고, 두 경로가 서로 다른
# 값을 말하면 어느 쪽도 고르지 않고 비운다.
#
# 왜 이렇게 하나: 경비는 한 달에 한 번이라 "표본을 쌓아 판단" 이 몇 달짜리가 된다.
# 그래서 지금 붙이는데, 대체가 아니라 **교차검증**으로 붙인다. 어긋나면 값을 비우고
# 기존 대상별 보류(`target_matching`)가 그 거래만 붙잡게 한다 - 화면 혼자 결정하는
# 지금보다 보수적이다.
#
# 비어 있는 것과 어긋나는 것은 다르다. 화면이 못 읽은 것은 모순이 아니라 공백이다.

import os
import time

_CACHE: dict[str, tuple[float, list[Entry] | None]] = {}

# 저장 직후 재조회가 묵은 값을 보지 않을 만큼 짧게. 그러면서 ready_report 의
# '3회 연속 같은 결과' 판정(500ms 간격 = 1.5초)보다 훨씬 길어야 한다 - 짧으면
# 폴링 중에 결과가 바뀌어 목록이 영원히 안정되지 않는다.
_TTL = 20.0
_TOLD: set[str] = set()


def disabled() -> bool:
    """끌 수 있어야 한다. 문서 없는 계층에 기대는 보완이다."""
    return bool(os.environ.get('AUTO_CONCUR_NO_GRAPHQL'))


def _say_once(key: str, message: str) -> None:
    if key not in _TOLD:
        _TOLD.add(key)
        print(message)


def _cached(ctx, report_id: str) -> list[Entry] | None:
    now = time.monotonic()
    hit = _CACHE.get(report_id)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    try:
        user_id, role = identify(ctx, report_id)
        found = entries(ctx, report_id, user_id, role)
    except GraphQLError as exc:
        # 진단이 본 작업을 막으면 안 된다. 화면 값으로 그냥 간다.
        _say_once('fail', f'  (GraphQL 보완을 쓰지 못했습니다: {exc})')
        _CACHE[report_id] = (now, None)
        return None
    _CACHE[report_id] = (now, found)
    return found


# 방금 쓴 경비. 쓰고 나면 두 계층이 잠깐 다르게 말한다 - 화면은 바로 바뀌는데
# 목록 질의는 따라오는 데 시간이 걸린다(2026-11-06 실측: 영수증을 붙인 직후
# 화면 True / GraphQL False). 그건 '어긋남' 이 아니라 전파 지연이다.
_JUST_WROTE: dict[str, float] = {}
_SETTLE = 90.0


def wrote(expense_id: str) -> None:
    if expense_id:
        _JUST_WROTE[expense_id] = time.monotonic()


def settling(expense_id: str) -> bool:
    at = _JUST_WROTE.get(expense_id)
    return at is not None and time.monotonic() - at < _SETTLE


def forget(report_id: str | None = None) -> None:
    """저장 뒤처럼 값이 바뀐 것을 아는 시점에 캐시를 버린다."""
    _CACHE.pop(report_id, None) if report_id else _CACHE.clear()


def _report_id_of(page) -> str | None:
    from .report_session import report_key
    from urllib.parse import urlsplit
    parts = urlsplit(page.url)
    path = parts.path.split('/expenses/')[0].rstrip('/')
    key = report_key(f'{parts.scheme}://{parts.netloc}{path}')
    return key[1] if key else None


def fill_gaps(page, rows: list) -> list:
    """화면이 못 읽은 날짜·금액·영수증만 채운다. 어긋나면 비운다.

    rows 는 `attach_receipts.Row` 목록이다. 제자리에서 고치고 그대로 돌려준다.

    **무슨 일이 있어도 예외를 올려보내지 않는다.** 이건 보완이고, 보완이 본
    작업을 막으면 지금까지 되던 것이 안 되게 된다. 그걸 '모든 접근을 다
    감쌌겠지' 로 믿지 않고 여기서 한 번 막는다.
    """
    try:
        return _fill(page, rows)
    except Exception as exc:
        _say_once('guard', f'  (GraphQL 보완을 건너뜁니다: {type(exc).__name__}: {exc})')
        return rows


def _fill(page, rows: list) -> list:
    if disabled() or not rows:
        return rows
    # 빈 자리가 있을 때만 묻지 않는다. 그러면 값이 다 찬 행의 '어긋남' 을 놓치고,
    # 교차검증이라는 목적 자체가 없어진다. 비용은 아래 캐시가 막는다 - 같은
    # 리포트를 _TTL 초 안에 다시 묻지 않으므로, 500ms 폴링 루프에서도 호출이
    # 늘지 않는다.
    report_id = _report_id_of(page)
    if not report_id:
        return rows
    found = _cached(page.context, report_id)
    if not found:
        return rows
    by_id = {e.expense_id: e for e in found if e.expense_id}
    filled, clashed, settled = [], [], []
    for row in rows:
        api = by_id.get(row.expense_id or '')
        if api is None:
            continue
        for what, attribute in (('날짜', 'when'), ('금액', 'amount'), ('영수증', 'has_receipt')):
            mine, theirs = getattr(row, attribute), getattr(api, attribute)
            if theirs is None:
                continue
            if mine is None:
                setattr(row, attribute, theirs)
                filled.append(f'{row.expense_id[:8]} {what}')
            elif mine != theirs:
                if settling(row.expense_id):
                    # 방금 쓴 경비다. 두 계층이 다른 것은 전파 지연이지 모순이
                    # 아니다. 화면이 더 최신이므로 화면 값을 둔다.
                    settled.append(f'{row.expense_id[:8]} {what}')
                    continue
                # 어느 쪽도 고르지 않는다. 비우면 대상별 보류가 받아 간다.
                setattr(row, attribute, None)
                clashed.append(f'{row.expense_id[:8]} {what}: 화면 {mine} / GraphQL {theirs}')
    if filled:
        print(f'  (GraphQL이 화면의 빈 값 {len(filled)}건을 채웠습니다: '
              + ', '.join(filled[:6]) + (' ...' if len(filled) > 6 else '') + ')')
    if settled:
        _say_once('settling', '  (방금 쓴 값이라 GraphQL이 아직 못 따라왔습니다 — 화면 값을 씁니다: '
                  + ', '.join(sorted(set(settled))[:4]) + ')')
    if clashed:
        print(f'  (두 경로가 어긋난 {len(clashed)}건은 값을 비웠습니다 — 해당 거래만 보류됩니다)')
        for line in clashed[:6]:
            print('    ' + line)
    return rows


# ---------------------------------------------------------------------------
# 쓰기 (2026-11-05). 저장과 영수증 첨부만.
#
# ## 질의문을 직접 썼다
#
# 읽기는 실측 질의문을 그대로 썼지만 `UpdateExistingExpenseEntry` 는 기록이 4000자에서
# 잘려서 재생할 수 없다. 그래서 **관측된 필드 이름만으로 최소 문서를 만들었다.**
# 원본이 끌어오는 거대한 프래그먼트와 `CDS_addRecentExpenseTypes`(policyId 필요)는
# 우리에게 필요 없다. 고른 필드는 전부 기록된 문서 본문에 실제로 있던 것이다.
#
# 보강 근거: DOM 쪽 셀렉터가 `#businessPurpose` / `textarea#comment` 이고
# GraphQL 의 `fields` 도 `businessPurpose` / `comment` 다. 이름이 같다.
#
# ## 보내기 전에 한 번 검증한다
#
# `@skip(if: $skip)` 로 같은 문서·같은 변수를 보내면 **문서와 변수는 검증되고
# resolver 는 실행되지 않는다.** 즉 쓰지 않고 모양만 확인할 수 있다. 실행당 한 번
# 돌려서, 내가 만든 문서가 틀렸으면 아무것도 건드리기 전에 안다.
#
# ## 실패했을 때 되돌아가지 않는다
#
# 보내기 전에 막힌 것(NotSent)과 보낸 뒤 결과를 모르는 것(MaybeSent)은 다르다.
# 전자는 화면 경로로 넘어가도 안전하지만, 후자는 **재시도도 폴백도 하지 않는다** -
# 둘 다 이중 저장이 될 수 있다. `concur_ui.click_target` 이 "클릭 결과를 확인할 수
# 없으면 재클릭하지 않는다" 로 같은 판단을 이미 하고 있다.

UPLOAD_ENDPOINT = 'https://www-eu2.api.concursolutions.com/spend-graphql/upload'

UPDATE_FIELDS = '''mutation UpdateExistingExpenseEntry($reportId: ID!, $expenseId: ID!, $contextRole: ContextRoleType!, $userId: ID!, $fields: UpdateExpenseFieldsInput!, $skip: Boolean! = false) {
  updateExpense(
    dataContext: {reportId: $reportId, expenseId: $expenseId, fields: $fields, shouldCopyDownFields: false}
    userContext: {userId: $userId, contextType: $contextRole}
  ) @skip(if: $skip) {
    id
    entry { id transactionDate transactionAmount { value currencyCode } }
    entryExceptions {
      countOfExceptions
      hasBlockingExceptions
      entryExceptions { exceptionCode isBlocking message parameters { missingFields { fields fieldIds } } }
    }
  }
}'''

# 이건 실측 문서 그대로다(__typename 만 덜어냈다).
ATTACH_IMAGE = '''mutation AttachImage($userId: String!, $contextRole: ContextRoleType!, $reportId: String!, $expenseId: String!, $imageId: String!, $skip: Boolean! = false) {
  employee(userId: $userId, contextRole: $contextRole) {
    expenseReport(reportId: $reportId) {
      entry(expenseId: $expenseId) {
        attachImage(imageId: $imageId) @skip(if: $skip) { id receiptImageId }
      }
    }
  }
}'''


class NotSent(GraphQLError):
    """보내기 전에 막혔다. 아무것도 쓰이지 않았으므로 화면 경로로 넘어가도 안전하다."""


class MaybeSent(GraphQLError):
    """보냈는데 결과를 확신할 수 없다. 재시도도 폴백도 하지 않는다 - 이중 저장이 된다."""


@dataclass
class Saved:
    expense_id: str
    blocking: bool
    messages: tuple[str, ...]
    missing: tuple[str, ...]


def _exceptions(node) -> Saved:
    """저장 응답에 같이 오는 예외를 읽는다. 응답이 곧 검증이다."""
    found = node or {}
    rows = found.get('entryExceptions') or []
    messages, missing = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('message'):
            messages.append(str(row['message']))
        fields = ((row.get('parameters') or {}).get('missingFields') or {})
        for name in (fields.get('fields') or []):
            missing.append(str(name))
    return Saved('', bool(found.get('hasBlockingExceptions')),
                 tuple(messages), tuple(dict.fromkeys(missing)))


def _write(ctx, name: str, query: str, variables: dict, *, dry: bool) -> dict:
    """쓰기 호출. 보냈는지 못 보냈는지를 예외 종류로 가린다."""
    payload = dict(variables, skip=dry)
    try:
        res = ctx.request.post(
            ENDPOINT, data={'operationName': name, 'query': query, 'variables': payload},
            headers={'content-type': 'application/json', 'accept': 'application/json'},
            timeout=30000)
    except Exception as exc:
        # 보냈는지 모른다. 타임아웃이 이미 서버에 닿은 뒤였을 수 있다.
        kind = NotSent if dry else MaybeSent
        raise kind(f'{name} 호출 실패: {type(exc).__name__}: {exc}') from exc
    if not res.ok:
        kind = NotSent if dry else MaybeSent
        raise kind(f'{name} 이(가) HTTP {res.status} 를 받았습니다' + _why(res))
    try:
        body = res.json()
    except Exception as exc:
        kind = NotSent if dry else MaybeSent
        raise kind(f'{name} 응답을 JSON으로 읽지 못했습니다') from exc
    errors = (body or {}).get('errors')
    if errors:
        first = errors[0].get('message') if isinstance(errors[0], dict) else str(errors[0])
        kind = NotSent if dry else MaybeSent
        raise kind(f'{name} 이(가) 오류를 돌려줬습니다: {first}')
    data = (body or {}).get('data')
    if not isinstance(data, dict):
        kind = NotSent if dry else MaybeSent
        raise kind(f'{name} 응답에 data 가 없습니다')
    return data


def ids(page) -> tuple[str, str, str]:
    """(reportId, userId, contextRole). 실패는 NotSent 다 - 아직 안 보냈다."""
    report_id = _report_id_of(page)
    if not report_id:
        raise NotSent('지금 화면에서 리포트 ID를 알 수 없습니다')
    try:
        user_id, role = identify(page.context, report_id)
    except GraphQLError as exc:
        raise NotSent(str(exc)) from exc
    return report_id, user_id, role


def save_fields(page, expense_id: str, fields: dict) -> Saved:
    """경비의 필드를 저장한다. 보낸 필드만 바뀐다(희소 업데이트).

    `fields` 는 예: {'businessPurpose': '...', 'comment': '...'}
    """
    if not expense_id or not fields:
        raise NotSent('저장할 경비 ID나 필드가 없습니다')
    report_id, user_id, role = ids(page)
    args = {'reportId': report_id, 'expenseId': expense_id, 'userId': user_id,
            'contextRole': role, 'fields': fields}
    # 쓰기 전에 문서·변수를 검증한다. resolver 는 실행되지 않는다.
    _write(page.context, 'UpdateExistingExpenseEntry', UPDATE_FIELDS, args, dry=True)
    data = _write(page.context, 'UpdateExistingExpenseEntry', UPDATE_FIELDS, args, dry=False)
    found = data.get('updateExpense')
    if not isinstance(found, dict):
        raise MaybeSent('저장 응답에 updateExpense 가 없습니다')
    forget(report_id)  # 목록 캐시가 묵은 값을 들고 있지 않게 한다
    wrote(expense_id)
    result = _exceptions(found.get('entryExceptions'))
    result.expense_id = expense_id
    return result


def attach_receipt(page, expense_id: str, path) -> str:
    """영수증 파일을 올리고 경비에 붙인다. 붙은 이미지 ID를 돌려준다."""
    from pathlib import Path
    target = Path(path)
    if not expense_id:
        raise NotSent('붙일 경비 ID가 없습니다')
    if not target.is_file():
        raise NotSent(f'영수증 파일이 없습니다: {target}')
    report_id, user_id, role = ids(page)

    suffix = target.suffix.lower()
    mime = {'.pdf': 'application/pdf', '.png': 'image/png', '.gif': 'image/gif',
            '.tif': 'image/tiff', '.tiff': 'image/tiff'}.get(suffix, 'image/jpeg')
    try:
        res = page.context.request.post(
            UPLOAD_ENDPOINT,
            multipart={'file': {'name': target.name, 'mimeType': mime,
                                'buffer': target.read_bytes()}},
            timeout=60000)
    except Exception as exc:
        # 업로드는 경비를 건드리지 않는다. 떠 있는 이미지가 생길 뿐이라 되돌아가도 안전하다.
        raise NotSent(f'영수증 업로드 실패: {type(exc).__name__}: {exc}') from exc
    if not res.ok:
        raise NotSent(f'영수증 업로드가 HTTP {res.status} 를 받았습니다' + _why(res))
    try:
        image_id = (res.json() or {}).get('imageId')
    except Exception as exc:
        raise NotSent('영수증 업로드 응답을 읽지 못했습니다') from exc
    if not image_id:
        raise NotSent('영수증 업로드 응답에 imageId 가 없습니다')

    args = {'reportId': report_id, 'expenseId': expense_id, 'userId': user_id,
            'contextRole': role, 'imageId': image_id}
    _write(page.context, 'AttachImage', ATTACH_IMAGE, args, dry=True)
    data = _write(page.context, 'AttachImage', ATTACH_IMAGE, args, dry=False)
    entry = (((data.get('employee') or {}).get('expenseReport') or {}).get('entry') or {})
    attached = (entry.get('attachImage') or {}).get('receiptImageId')
    if not attached:
        raise MaybeSent('첨부 응답에 receiptImageId 가 없습니다')
    forget(report_id)
    wrote(expense_id)
    return attached


# 숙박 '가격 동일' 명세. 실측 문서(2026-11-05, 10599자)에서 우리가 쓰는 부분만
# 남겼다 - 원본이 끌어오는 프래그먼트 일곱 개는 필요 없다.
#
# `roomRateExpenseTypeId` 는 원본 문서에서 `String!` 로 선언되지만 **필드 인자
# 자체는 선택**이다(2026-11-05 시험에서 확인. 대조군 4건이 전부 거부된 유효한
# 판이었다). 그래서 뺐다 - 그 값의 출처를 아직 못 찾았는데, 안 보내도 된다.
CREATE_SAME_ROOM_RATE = '''mutation CreateSameRoomRateMutation($reportId: String!, $expenseId: String!, $userId: String!, $contextRole: ContextRoleType!, $roomRateObject: RoomRateAndTax!, $skip: Boolean! = false) {
  createSameRoomRateItemizations(
    reportId: $reportId
    expenseId: $expenseId
    userId: $userId
    contextRole: $contextRole
    roomRateObject: $roomRateObject
  ) @skip(if: $skip) {
    status { success }
    entry { id transactionAmount { value currencyCode } }
    entryExceptions {
      countOfExceptions
      hasBlockingExceptions
      entryExceptions { exceptionCode isBlocking message parameters { missingFields { fields fieldIds } } }
    }
  }
}'''


def itemized(page, expense_id: str) -> bool | None:
    """이 경비에 이미 명세가 있나. **모르면 None 이고, 모르면 만들지 않는다.**

    이 mutation 은 명세를 '만든다'. 이미 있는데 또 만들면 중복이 된다. 그래서
    아니라고 확신할 때만 쓴다.
    """
    try:
        report_id = _report_id_of(page)
        found = _cached(page.context, report_id) if report_id else None
    except Exception:
        return None
    for entry in found or []:
        if entry.expense_id == expense_id:
            return entry.has_itemizations
    return None


# 1박 요금이 제각각인 명세. 실측 문서(2026-11-05, 10612자)에서 쓰는 부분만 남겼다.
#
# `SameRoomRate` 와 다른 점은 `roomRateObject` 가 **리스트**라는 것 하나다.
# 20만원 3박이면 [{66667}, {66667}, {66666}] 을 보낸다 - `nightly_split` 결과
# 그대로다. 합이 경비 총액과 정확히 맞으므로 '가격 동일' 로 보낼 때처럼 1원이
# 넘치거나 모자라지 않는다.
CREATE_DIFFERENT_ROOM_RATE = '''mutation CreateDifferentRoomRateMutation($reportId: String!, $expenseId: String!, $userId: String!, $contextRole: ContextRoleType!, $roomRateObject: [RoomRateAndTax!]!, $skip: Boolean! = false) {
  createDifferentRoomRateItemizations(
    reportId: $reportId
    expenseId: $expenseId
    userId: $userId
    contextRole: $contextRole
    roomRateObject: $roomRateObject
  ) @skip(if: $skip) {
    status { success }
    entry { id transactionAmount { value currencyCode } }
    entryExceptions {
      countOfExceptions
      hasBlockingExceptions
      entryExceptions { exceptionCode isBlocking message parameters { missingFields { fields fieldIds } } }
    }
  }
}'''


def itemize(page, expense_id: str, nightly: list[int]) -> Saved:
    """숙박 명세를 만든다. 1박 요금이 같으면 Same, 다르면 Different 로 간다.

    `nightly` 는 `sheet.nightly_split` 결과다 - 합이 경비 총액과 정확히 맞는다.
    """
    if not expense_id or not nightly or any(
            not isinstance(n, int) or isinstance(n, bool) or n <= 0 for n in nightly):
        raise NotSent('명세를 만들 경비 ID나 1박 요금이 없습니다')
    report_id, user_id, role = ids(page)
    args = {'reportId': report_id, 'expenseId': expense_id, 'userId': user_id,
            'contextRole': role}
    if len(set(nightly)) == 1:
        name, query = 'CreateSameRoomRateMutation', CREATE_SAME_ROOM_RATE
        args['roomRateObject'] = {'roomRate': nightly[0]}
        root = 'createSameRoomRateItemizations'
    else:
        name, query = 'CreateDifferentRoomRateMutation', CREATE_DIFFERENT_ROOM_RATE
        args['roomRateObject'] = [{'roomRate': n} for n in nightly]
        root = 'createDifferentRoomRateItemizations'
    _write(page.context, name, query, args, dry=True)
    data = _write(page.context, name, query, args, dry=False)
    found = data.get(root)
    if not isinstance(found, dict):
        raise MaybeSent(f'명세 응답에 {root} 가 없습니다')
    if (found.get('status') or {}).get('success') is False:
        raise MaybeSent('Concur가 명세 생성을 실패로 표시했습니다')
    forget(report_id)
    result = _exceptions(found.get('entryExceptions'))
    result.expense_id = expense_id
    return result


# ---------------------------------------------------------------------------
# 참석자 (2026-11-05). 전에 실제로 데이터를 날린 자리라 가장 보수적으로 간다.
#
# 세 걸음이다.
#   1. `Attendee.GetAttendeeTypes` (구형 .NET 라우터) -> atnTypeKey 목록
#   2. `attendeesByPredictiveSearch(searchTerms, ...)` -> 후보
#   3. `SaveExpenseAttendees` -> 저장
#
# **`saveAttendeesLegacy` 는 목록을 통째로 교체한다.** 그게 전에 참석자가 다
# 지워진 사고의 메커니즘이다. 그래서 보낼 목록을 확신할 때만 보낸다 - 한 명이라도
# 유일하게 못 고르면 아무것도 보내지 않는다.

LEGACY_PATH = '/expense/expenseDotNet/Proxy/expenseRouter.ashx'

# 실측 헤더(2026-11-05). 응답 모양을 바꾸는 스위치라 값이 필요하다.
LEGACY_HEADERS = {'content-type': 'application/json',
                  'camelcase': 'Y', 'fixjson': 'Y', 'x-nui': 'Y'}

SEARCH_ATTENDEES = '''query GetAttendeesByPredictiveSearch($searchTerms: String!, $includeInactiveEmp: Boolean!, $includeAtnTypeKeys: [Int!]!, $excludeKeys: [String!]!) {
  attendeesByPredictiveSearch(
    searchTerms: $searchTerms
    includeInactiveEmp: $includeInactiveEmp
    includeAtnTypeKeys: $includeAtnTypeKeys
    excludeKeys: $excludeKeys
  ) {
    attendees {
      atnKey
      atnTypeCode
      versionNumber
      firstName
      lastName
      preferredName
      emailAddress
    }
  }
}'''

SAVE_ATTENDEES = '''mutation SaveExpenseAttendees($userId: String!, $contextRole: ContextRoleType!, $params: SaveAttendeesLegacyParams!, $skip: Boolean! = false) {
  employee(userId: $userId, contextRole: $contextRole) {
    saveAttendeesLegacy(params: $params) @skip(if: $skip) { status }
  }
}'''


def legacy(page, action: str, method: str, data) -> dict:
    """구형 .NET 라우터를 부른다. 읽기 전용으로만 쓴다."""
    from urllib.parse import urlsplit
    host = urlsplit(page.url).netloc
    if not host.endswith('.concursolutions.com'):
        raise NotSent(f'예상 밖의 호스트입니다: {host}')
    try:
        res = page.context.request.post(
            f'https://{host}{LEGACY_PATH}',
            data={'action': action, 'method': method, 'data': data},
            headers=LEGACY_HEADERS, timeout=30000)
    except Exception as exc:
        raise NotSent(f'{action}.{method} 호출 실패: {type(exc).__name__}') from exc
    if not res.ok:
        raise NotSent(f'{action}.{method} 이(가) HTTP {res.status} 를 받았습니다' + _why(res))
    try:
        return res.json() or {}
    except Exception as exc:
        raise NotSent(f'{action}.{method} 응답을 JSON으로 읽지 못했습니다') from exc


def attendee_type_keys(page, user_id: str) -> list[int]:
    """검색에 넘길 참석자 유형 키. `forEmpKey` 가 userId 와 같은 값이라고 본다.

    둘 다 같은 꼴(len33-128)이라 그렇게 추정한다. 틀리면 records 가 비어서
    이 함수가 멈추고, 부르는 쪽이 화면 경로로 간다 - 조용히 빈 목록으로
    검색하지 않는다(그러면 후보가 안 나와서 참석자를 못 찾는다).
    """
    body = legacy(page, 'Attendee', 'GetAttendeeTypes', {'forEmpKey': user_id})
    records = ((body.get('attendee_GetAttendeeTypes') or {}).get('records')) or []
    keys = [r['atnTypeKey'] for r in records
            if isinstance(r, dict) and isinstance(r.get('atnTypeKey'), int)
            and str(r.get('isDeleted', 'N')).upper() != 'Y']
    if not keys:
        raise NotSent('참석자 유형 목록이 비어 있습니다 (forEmpKey 가 다를 수 있습니다)')
    return keys


def _local_part(text: str) -> str:
    head, _, _ = (text or '').partition('@')
    return head.strip().casefold()


def search_attendees(page, terms: str, type_keys: list[int]) -> list[dict]:
    data = call(page.context, 'GetAttendeesByPredictiveSearch', SEARCH_ATTENDEES,
                {'searchTerms': terms, 'includeInactiveEmp': False,
                 'includeAtnTypeKeys': type_keys, 'excludeKeys': []})
    found = (data.get('attendeesByPredictiveSearch') or {}).get('attendees')
    return [a for a in (found or []) if isinstance(a, dict) and a.get('atnKey')]


def pick_attendee(query: str, found: list[dict]):
    """후보 중 **유일하게** 맞는 한 명. 아니면 None.

    메일 주소를 적었으면 `emailAddress` 로 정확히 맞춘다. 화면 이름을 토막 내서
    맞추던 것보다 확실하다 - 'kyungsik.oh' 가 'Oh Kyungsik' 과 안 맞던 문제가
    여기서는 생기지 않는다.
    """
    from .fix_expenses import name_matches
    want = _local_part(query)
    exact = [a for a in found if _local_part(a.get('emailAddress') or '') == want and want]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return None  # 여럿이면 고르지 않는다
    def shown(a):
        full = ' '.join(x for x in (a.get('firstName'), a.get('lastName')) if x)
        return [x for x in (full, a.get('preferredName'), a.get('emailAddress')) if x]
    loose = [a for a in found if any(name_matches(query, text) for text in shown(a))]
    return loose[0] if len(loose) == 1 else None


def save_attendees(page, expense_id: str, chosen: list[dict], amounts: list[int]) -> Saved:
    """참석자 목록을 **통째로 교체**한다. 보낼 목록을 확신할 때만 부른다."""
    if not expense_id or not chosen:
        raise NotSent('저장할 참석자가 없습니다')
    if len(chosen) != len(amounts):
        raise NotSent('참석자 수와 금액 배분이 맞지 않습니다')
    report_id, user_id, role = ids(page)
    attendees = [{'atnKey': a['atnKey'], 'atnTypeCode': a.get('atnTypeCode'),
                  'versionNumber': a.get('versionNumber'), 'instanceCount': 1,
                  'transactionAmount': amount}
                 for a, amount in zip(chosen, amounts)]
    args = {'userId': user_id, 'contextRole': role,
            'params': {'reportId': report_id, 'entryId': expense_id, 'attendees': attendees}}
    _write(page.context, 'SaveExpenseAttendees', SAVE_ATTENDEES, args, dry=True)
    _write(page.context, 'SaveExpenseAttendees', SAVE_ATTENDEES, args, dry=False)
    forget(report_id)
    return Saved(expense_id, False, (), ())


# ---------------------------------------------------------------------------
# 자동차 마일리지 생성 (2026-11-05).
#
# **이건 '고치기' 가 아니라 '만들기' 다.** 틀리면 없던 재무 기록이 생기고 사람이
# 찾아 지워야 한다. 그래서 더 조인다 - 폼이 '저장에 필요하다'고 한 칸 중 하나라도
# 값을 만들지 못하면 **아무것도 보내지 않고** 화면 경로로 간다.
#
# 조회를 따로 만들지 않는다. 화면이 하는 방식 그대로다:
#   1. GetExpenseTypesForReport  -> 마일리지 경비유형 이름 -> id
#   2. GetNewExpenseEntry(그 id) -> 폼 42칸. 기본값도 차량 옵션도 여기 다 있다
#   3. 폼의 기본값에 우리 값(차량·날짜·출발/도착지·거리)만 덮는다
#   4. SaveNewExpenseEntry
#
# 차량은 그 폼의 `vehicleId` 칸 `options[{id, value}]` 에서 표시 이름으로 고른다.
# 목록을 따로 받아올 필요가 없다.

REPORT_POLICY = '''query GetReportIdAndKey($userId: String, $contextRole: ContextRoleType, $reportId: String) {
  employee(userId: $userId, contextRole: $contextRole) {
    expenseReport(reportId: $reportId) {
      reportId
      reportDetails { id policy { id } }
    }
  }
}'''

EXPENSE_TYPES = '''query GetExpenseTypesForReport($userId: ID!, $contextRole: ContextRoleType!, $policyId: ID!, $reportId: ID!, $reportOwnerUserId: ID!) {
  expenseTypesForReport(
    userId: $userId
    contextRole: $contextRole
    policyId: $policyId
    reportId: $reportId
    reportOwnerUserId: $reportOwnerUserId
  ) { id code name text }
}'''

NEW_EXPENSE_FORM = '''query GetNewExpenseEntry($reportId: String!, $userId: String!, $expenseTypeId: ID!, $contextRole: ContextRoleType!) {
  employee(userId: $userId, contextRole: $contextRole) {
    expenseReport(reportId: $reportId) {
      newExpenseForm(expenseTypeId: $expenseTypeId) {
        mainForm {
          fields {
            id
            label
            control
            dataType
            isRequired
            requiredForSave
            options { id code value }
            value {
              listValue { id code value }
              dateValue
              stringValue
              integerValue
              booleanValue
              operation
              value
              amountValue { value currencyCode }
            }
            defaultValue { value code listItemId }
          }
        }
      }
    }
  }
}'''

CREATE_EXPENSE = '''mutation SaveNewExpenseEntry($reportId: ID!, $contextRole: ContextRoleType!, $userId: ID!, $fields: CreateExpenseFieldsInput!, $skip: Boolean! = false) {
  createExpense(
    dataContext: {reportId: $reportId, fields: $fields}
    userContext: {userId: $userId, contextType: $contextRole}
  ) @skip(if: $skip) {
    id
    entry { id transactionDate transactionAmount { value currencyCode } }
    entryExceptions {
      countOfExceptions
      hasBlockingExceptions
      entryExceptions { exceptionCode isBlocking message parameters { missingFields { fields fieldIds } } }
    }
  }
}'''


def policy_id(page, report_id: str, user_id: str, role: str) -> str:
    data = call(page.context, 'GetReportIdAndKey', REPORT_POLICY,
                {'userId': user_id, 'contextRole': role, 'reportId': report_id})
    found = (((data.get('employee') or {}).get('expenseReport') or {})
             .get('reportDetails') or {}).get('policy') or {}
    if not found.get('id'):
        raise NotSent('리포트의 정책 ID를 받지 못했습니다')
    return found['id']


def expense_type_id(page, name: str, report_id: str, user_id: str, role: str) -> str:
    """경비유형 표시 이름 -> id. **유일하게 맞을 때만** 준다."""
    pid = policy_id(page, report_id, user_id, role)
    data = call(page.context, 'GetExpenseTypesForReport', EXPENSE_TYPES,
                {'userId': user_id, 'contextRole': role, 'policyId': pid,
                 'reportId': report_id, 'reportOwnerUserId': user_id})
    types = data.get('expenseTypesForReport') or []
    want = (name or '').strip().casefold()

    def shown(t):
        return [str(t.get(k) or '').strip() for k in ('name', 'text', 'code')]

    hit = [t for t in types if want in {x.casefold() for x in shown(t)}]
    if not hit:
        # 화면에 보이는 이름과 저장소가 들고 있는 이름이 조금 다를 수 있다
        # (2026-11-06 실측: '자동차 마일리지' 가 40개 중 정확히 맞는 것이 없었다).
        # 포함으로 넓히되 **유일할 때만** 받는다.
        hit = [t for t in types if any(want and want in x.casefold() for x in shown(t))]
    if len(hit) != 1:
        near = [x for t in (hit or types) for x in shown(t) if x][:8]
        raise NotSent(f'경비유형 {name!r} 를 {len(types)}개 중 유일하게 고르지 못했습니다'
                      + (f' (후보: {", ".join(near)})' if near else ''))
    return hit[0]['id']


def new_form_fields(page, expense_type_id_: str, report_id: str,
                    user_id: str, role: str) -> list[dict]:
    data = call(page.context, 'GetNewExpenseEntry', NEW_EXPENSE_FORM,
                {'reportId': report_id, 'userId': user_id, 'contextRole': role,
                 'expenseTypeId': expense_type_id_})
    form = ((((data.get('employee') or {}).get('expenseReport') or {})
             .get('newExpenseForm') or {}).get('mainForm') or {})
    fields = form.get('fields')
    if not isinstance(fields, list) or not fields:
        raise NotSent('새 경비 폼의 입력 칸을 받지 못했습니다')
    return fields


def option_of(fields: list[dict], field_id: str, label: str):
    """폼 칸의 options 에서 표시 이름으로 고른다. 유일할 때만."""
    one = next((f for f in fields if f.get('id') == field_id), None)
    if one is None:
        raise NotSent(f'폼에 {field_id} 칸이 없습니다')
    want = (label or '').strip().casefold()
    hit = [o for o in (one.get('options') or [])
           if str(o.get('value') or '').strip().casefold() == want]
    if len(hit) != 1:
        count = len(one.get('options') or [])
        raise NotSent(f'{field_id} 에서 {label!r} 를 {count}개 중 유일하게 고르지 못했습니다')
    return hit[0]['id']


def _field_value(one: dict):
    """폼 칸이 들고 있는 값을 입력 모양으로 바꾼다. 없으면 None."""
    value = one.get('value') or {}
    listed = value.get('listValue') or {}
    if listed.get('id'):
        return listed['id']
    for key in ('dateValue', 'stringValue', 'integerValue'):
        if value.get(key) not in (None, ''):
            return value[key]
    if value.get('booleanValue') is not None:
        return value['booleanValue']
    amount = value.get('amountValue') or {}
    if amount.get('value') is not None:
        return {'value': amount['value'], 'currencyCode': amount.get('currencyCode')}
    if value.get('operation') is not None and value.get('value') is not None:
        return {'operation': value['operation'], 'value': value['value']}
    fallback = (one.get('defaultValue') or {}).get('value')
    return fallback if fallback not in (None, '') else None


def create_mileage(page, type_name: str, vehicle: str, when, origin: str,
                   destination: str, distance, passengers=None,
                   description: str = '') -> Saved:
    """마일리지 경비를 만든다. 금액은 보내지 않는다 - Concur 가 요율로 계산한다.

    우리가 계산해 보내면 **회사 정책 요율을 덮어쓸 수 있다.** 대신 응답의
    `entry.transactionAmount` 로 계산됐는지 확인하고, 0이면 그대로 알린다.
    """
    report_id, user_id, role = ids(page)
    type_id = expense_type_id(page, type_name, report_id, user_id, role)
    fields = new_form_fields(page, type_id, report_id, user_id, role)

    ours = {
        'expenseTypeId': type_id,
        'vehicleId': option_of(fields, 'vehicleId', vehicle),
        'transactionDate': when.isoformat() if hasattr(when, 'isoformat') else str(when),
        'startLocation': origin,
        'endLocation': destination,
        'totalDistance': int(distance),
    }
    if passengers not in (None, ''):
        ours['passengerCount'] = int(passengers)
    if description:
        ours['comment'] = description
    # 폼이 '저장에 필요하다'고 한 칸은 폼의 값을 그대로 쓴다. 하나라도 값을
    # 만들지 못하면 반쯤 만든 경비를 남기지 않도록 여기서 멈춘다.
    missing = []
    for one in fields:
        key = one.get('id')
        if not key or key in ours or not one.get('requiredForSave'):
            continue
        got = _field_value(one)
        if got is None:
            missing.append(f"{key}({one.get('label') or ''})")
        else:
            ours[key] = got
    if missing:
        raise NotSent('새 경비 폼의 필수 칸을 채우지 못했습니다: ' + ', '.join(missing[:6]))

    args = {'reportId': report_id, 'userId': user_id, 'contextRole': role, 'fields': ours}
    _write(page.context, 'SaveNewExpenseEntry', CREATE_EXPENSE, args, dry=True)
    data = _write(page.context, 'SaveNewExpenseEntry', CREATE_EXPENSE, args, dry=False)
    made = data.get('createExpense')
    if not isinstance(made, dict) or not made.get('id'):
        raise MaybeSent('생성 응답에 createExpense.id 가 없습니다')
    forget(report_id)
    result = _exceptions(made.get('entryExceptions'))
    result.expense_id = made['id']
    amount = ((made.get('entry') or {}).get('transactionAmount') or {}).get('value')
    if not amount:
        result.messages = result.messages + (
            '금액이 계산되지 않았습니다 (거리·요율 설정을 확인해 주세요)',)
    return result


# ---------------------------------------------------------------------------
# 숙박 상세 (2026-11-05). 날짜·숙박위치·Booking channel.
#
# 숙박위치(`custom10`)와 Booking channel(`custom16`)은 둘 다 목록 칸이고, 선택지가
# 폼 안에 `options {id, code, value}` 로 들어 있다. 차량 때와 같다 - 목록을 따로
# 받아올 필요가 없다.
#
# 날짜는 `hotelCheckinDate`/`hotelCheckoutDate` 로 ISO 문자열이다. 화면의 날짜 범위
# 입력칸(과 "날짜 범위 닫기" 로 죽던 길)이 통째로 사라진다.

EXISTING_FORM = '''query GetExistingExpenseEntry($expenseIdAsID: ID!, $reportIdAsID: ID!, $userIdAsID: ID!, $contextRole: ContextRoleType!, $showAttendeeField: Boolean! = false) {
  existingExpenseForm(
    dataContext: {expenseId: $expenseIdAsID, reportId: $reportIdAsID}
    userContext: {userId: $userIdAsID, contextType: $contextRole}
  ) {
    expenseId
    mainForm(showAttendeeField: $showAttendeeField) {
      fields {
        id
        label
        control
        dataType
        isRequired
        requiredForSave
        options { id code value }
        value {
          ... on ListValue { listValue: value { code id value } }
          ... on ListItemValue { code id listItemValue: value }
          ... on DateValue { dateValue: value }
          ... on StringValue { stringValue: value }
        }
      }
    }
  }
}'''


def existing_form_fields(page, expense_id: str) -> list[dict]:
    report_id, user_id, role = ids(page)
    data = call(page.context, 'GetExistingExpenseEntry', EXISTING_FORM,
                {'expenseIdAsID': expense_id, 'reportIdAsID': report_id,
                 'userIdAsID': user_id, 'contextRole': role})
    form = ((data.get('existingExpenseForm') or {}).get('mainForm') or {})
    fields = form.get('fields')
    if not isinstance(fields, list) or not fields:
        raise NotSent('경비 상세 폼의 입력 칸을 받지 못했습니다')
    return fields


def list_choice(fields: list[dict], field_id: str, label: str) -> str:
    """목록 칸에서 표시 이름으로 고른다. 유일할 때만. 없으면 멈춘다."""
    one = next((f for f in fields if f.get('id') == field_id), None)
    if one is None:
        raise NotSent(f'폼에 {field_id} 칸이 없습니다')
    want = (label or '').strip().casefold()
    hit = [o for o in (one.get('options') or [])
           if want in {str(o.get(k) or '').strip().casefold() for k in ('value', 'code')}]
    if len(hit) != 1:
        count = len(one.get('options') or [])
        raise NotSent(f"{one.get('label') or field_id} 에서 {label!r} 를 "
                      f'{count}개 중 유일하게 고르지 못했습니다')
    return hit[0]['id']


def lodging_fields(page, expense_id: str, checkin, checkout,
                   location: str = '', channel: str = '') -> dict:
    """숙박 상세에 넣을 `fields`. 하나라도 못 고르면 멈춘다 - 반만 넣지 않는다."""
    wanted = {}
    if checkin and checkout:
        wanted['hotelCheckinDate'] = checkin.isoformat()
        wanted['hotelCheckoutDate'] = checkout.isoformat()
    if location or channel:
        fields = existing_form_fields(page, expense_id)
        # 목록 칸은 {listItemId, value} 로 받는다(실측). 둘 다 선택지의 id 다.
        for field_id, label in (('custom10', location), ('custom16', channel)):
            if not label:
                continue
            chosen = list_choice(fields, field_id, label)
            wanted[field_id] = {'listItemId': chosen, 'value': chosen}
    if not wanted:
        raise NotSent('숙박 상세에 넣을 값이 없습니다')
    return wanted
