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
    filled, clashed = [], []
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
                # 어느 쪽도 고르지 않는다. 비우면 대상별 보류가 받아 간다.
                setattr(row, attribute, None)
                clashed.append(f'{row.expense_id[:8]} {what}: 화면 {mine} / GraphQL {theirs}')
    if filled:
        print(f'  (GraphQL이 화면의 빈 값 {len(filled)}건을 채웠습니다: '
              + ', '.join(filled[:6]) + (' ...' if len(filled) > 6 else '') + ')')
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
    return attached
