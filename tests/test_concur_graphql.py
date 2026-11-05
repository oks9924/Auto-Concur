"""GraphQL 읽기 경로. 실제 Concur 없이 가짜 응답으로 검증한다.

핵심은 '모르는 것을 추측으로 메우지 않는다'다. 문서 없는 계층이라 응답 모양이
바뀌면 알아야 하고, 조용히 메우면 틀린 값을 쓰게 된다.
"""
from datetime import date

import pytest

from src import concur_graphql as gq
from src.attach_receipts import Row
from src.compare_rows import differences, report_lines


class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeRequest:
    def __init__(self, body, status=200):
        self.body, self.status, self.sent = body, status, []

    def post(self, url, **kw):
        self.sent.append((url, kw))
        return FakeResponse(self.body, self.status)


class FakeContext:
    def __init__(self, body, status=200):
        self.request = FakeRequest(body, status)


ENTRY = {
    'expenseId': 'E1',
    'summary': {
        'id': 'E1', 'parentExpenseId': None,
        'transactionDate': '2026-09-07',
        'transactionAmount': {'value': 450000, 'currencyCode': 'KRW'},
        'vendor': {'description': '라한호텔울산', 'name': None},
        'expenseType': {'id': '1', 'code': 'LODNG', 'name': '숙박비'},
        'receiptImageId': 'IMG1', 'eReceiptImageId': None, 'isImageRequired': True,
        'meta': {'hasReceiptImage': True, 'hasSourceCreditCard': True, 'hasSourceEReceipt': False},
    },
}


def listing(entries, report_id='R1'):
    return {'data': {'reportEntriesDetails': {'reportId': report_id, 'entries': entries}}}


# --- 실패를 조용히 넘기지 않는다 ------------------------------------------

def test_HTTP_오류는_예외다():
    with pytest.raises(gq.GraphQLError, match='HTTP 403'):
        gq.call(FakeContext({}, 403), 'X', 'query X { x }', {})


def test_GraphQL_오류_본문은_메시지를_보여준다():
    ctx = FakeContext({'errors': [{'message': 'Field "x" not found'}]})
    with pytest.raises(gq.GraphQLError, match='Field "x" not found'):
        gq.call(ctx, 'X', 'query X { x }', {})


def test_data_가_없으면_예외다():
    with pytest.raises(gq.GraphQLError, match='data 가 없습니다'):
        gq.call(FakeContext({'nothing': 1}), 'X', 'query X { x }', {})


def test_JSON이_아니면_예외다():
    with pytest.raises(gq.GraphQLError, match='JSON으로 읽지 못했습니다'):
        gq.call(FakeContext(ValueError('not json')), 'X', 'query X { x }', {})


# --- 세션 확인 -------------------------------------------------------------

def test_세션에서_userId_와_역할을_받는다():
    ctx = FakeContext({'data': {'employee': {
        'userId': 'U1', 'contextRole': 'TRAVELER',
        'expenseReport': {'reportId': 'R1', 'rptKey': 'K1'}}}})
    assert gq.identify(ctx, 'R1') == ('U1', 'TRAVELER')
    url, kw = ctx.request.sent[0]
    assert url == gq.ENDPOINT
    assert kw['data']['variables']['userId'] is None     # 세션이 스스로 답한다


def test_리포트가_안_잡히면_다음_할_일을_알려준다():
    """주소창의 ID가 GraphQL의 reportId가 아닐 수 있다. 제일 있을 법한 실패다."""
    ctx = FakeContext({'data': {'employee': {
        'userId': 'U1', 'contextRole': 'TRAVELER', 'expenseReport': None}}})
    with pytest.raises(gq.GraphQLError, match='rptKey'):
        gq.identify(ctx, 'R1')


def test_다른_리포트가_오면_멈춘다():
    """엉뚱한 리포트를 읽으면 비교가 무의미하다. check_context 와 같은 규율이다."""
    ctx = FakeContext({'data': {'employee': {
        'userId': 'U1', 'contextRole': 'TRAVELER',
        'expenseReport': {'reportId': 'OTHER', 'rptKey': 'K1'}}}})
    with pytest.raises(gq.GraphQLError, match='리포트가 다릅니다'):
        gq.identify(ctx, 'R1')


# --- 목록 읽기 -------------------------------------------------------------

def test_행을_기계값으로_읽는다():
    got = gq.entries(FakeContext(listing([ENTRY])), 'R1', 'U1', 'TRAVELER')
    assert len(got) == 1
    row = got[0]
    assert row.expense_id == 'E1'
    assert row.when == date(2026, 9, 7)      # 서식 추론이 필요 없다
    assert row.amount == 450000
    assert row.vendor == '라한호텔울산'
    assert row.expense_type == '숙박비'
    assert row.has_receipt is True
    assert row.note == ''


def test_ISO가_아닌_날짜는_지어내지_않는다():
    bad = {**ENTRY, 'summary': {**ENTRY['summary'], 'transactionDate': '09/07/2026'}}
    row = gq.entries(FakeContext(listing([bad])), 'R1', 'U1', 'TRAVELER')[0]
    assert row.when is None and 'ISO 날짜가 아님' in row.note
    assert row.raw_date == '09/07/2026'      # 원문은 남긴다


def test_KRW가_아니면_금액을_쓰지_않는다():
    usd = {**ENTRY, 'summary': {**ENTRY['summary'],
                                'transactionAmount': {'value': 100, 'currencyCode': 'USD'}}}
    row = gq.entries(FakeContext(listing([usd])), 'R1', 'U1', 'TRAVELER')[0]
    assert row.amount is None and 'KRW가 아님' in row.note


def test_소수_금액은_반올림하지_않는다():
    frac = {**ENTRY, 'summary': {**ENTRY['summary'],
                                 'transactionAmount': {'value': 170.5, 'currencyCode': 'KRW'}}}
    row = gq.entries(FakeContext(listing([frac])), 'R1', 'U1', 'TRAVELER')[0]
    assert row.amount is None and '정수가 아님' in row.note


def test_소수부가_0이면_정수로_받는다():
    whole = {**ENTRY, 'summary': {**ENTRY['summary'],
                                  'transactionAmount': {'value': 17000.0, 'currencyCode': 'KRW'}}}
    assert gq.entries(FakeContext(listing([whole])), 'R1', 'U1', 'TRAVELER')[0].amount == 17000


def test_entries_가_배열이_아니면_예외다():
    broken = {'data': {'reportEntriesDetails': {'reportId': 'R1', 'entries': None}}}
    with pytest.raises(gq.GraphQLError, match='entries 배열이 없습니다'):
        gq.entries(FakeContext(broken), 'R1', 'U1', 'TRAVELER')


def test_목록_응답의_리포트가_다르면_예외다():
    with pytest.raises(gq.GraphQLError, match='리포트 ID가 요청과 다릅니다'):
        gq.entries(FakeContext(listing([ENTRY], 'OTHER')), 'R1', 'U1', 'TRAVELER')


def test_명세_자식_행은_목록에서_뺀다():
    """화면 목록에는 부모만 나온다. 안 빼면 첫 실행이 전부 불일치로 보인다."""
    child = {'expenseId': 'C1', 'summary': {**ENTRY['summary'], 'id': 'C1',
                                            'parentExpenseId': 'E1'}}
    every = gq.entries(FakeContext(listing([ENTRY, child])), 'R1', 'U1', 'TRAVELER')
    assert len(every) == 2
    assert [e.expense_id for e in gq.top_level(every)] == ['E1']


# --- 비교 ------------------------------------------------------------------

def dom(expense_id, when, amount, receipt=True):
    return Row(index=0, when=when, amount=amount, text='', expense_id=expense_id,
               has_receipt=receipt)


def api(expense_id, when, amount, receipt=True, note=''):
    return gq.Entry(expense_id=expense_id, when=when, amount=amount,
                    has_receipt=receipt, note=note)


def test_같으면_차이가_없다():
    assert differences([dom('E1', date(2026, 9, 7), 450000)],
                       [api('E1', date(2026, 9, 7), 450000)]) == []


def test_화면이_못_읽은_날짜가_차이로_드러난다():
    """'10/09/2026 을 못 읽어 보류' 가 바로 이 자리다."""
    gaps = differences([dom('E1', None, 450000)],
                       [api('E1', date(2026, 9, 7), 450000)])
    assert len(gaps) == 1
    assert gaps[0]['what'] == '날짜'
    assert gaps[0]['화면'] == 'None' and gaps[0]['GraphQL'] == '2026-09-07'


def test_한쪽에만_있는_경비를_알린다():
    gaps = differences([dom('E1', date(2026, 9, 7), 1)], [api('E2', date(2026, 9, 7), 1)])
    assert {g['what'] for g in gaps} == {'화면에만 있음', 'GraphQL에만 있음'}


def test_영수증_판정_차이도_잡는다():
    gaps = differences([dom('E1', date(2026, 9, 7), 1, receipt=True)],
                       [api('E1', date(2026, 9, 7), 1, receipt=False)])
    assert [g['what'] for g in gaps] == ['영수증']


def test_요약은_못_읽은_행_수를_센다():
    lines = report_lines([dom('E1', None, None)], [api('E1', date(2026, 9, 7), 1)], children=2)
    assert '명세 자식 2행은 제외' in lines[0]
    assert '화면 1행 · GraphQL 0행' in lines[1]


# --- 스키마 조회 (읽기 전용) -------------------------------------------------

def test_타입_포장을_읽는_꼴로_편다():
    from src.inspect_schema import render
    assert render({'kind': 'NON_NULL', 'ofType': {'kind': 'SCALAR', 'name': 'String'}}) == 'String!'
    assert render({'kind': 'LIST', 'ofType': {'kind': 'NON_NULL',
                   'ofType': {'kind': 'INPUT_OBJECT', 'name': 'AttendeeInput'}}}) == '[AttendeeInput!]'
    assert render(None) == '?'


def test_필수와_선택을_가른다():
    from src.inspect_schema import describe
    lines = '\n'.join(describe({'name': 'T', 'kind': 'INPUT_OBJECT', 'inputFields': [
        {'name': 'atnKey', 'defaultValue': None,
         'type': {'kind': 'NON_NULL', 'ofType': {'kind': 'SCALAR', 'name': 'String'}}},
        {'name': 'transactionAmount', 'defaultValue': None,
         'type': {'kind': 'SCALAR', 'name': 'Float'}},
        {'name': 'instanceCount', 'defaultValue': '1',
         'type': {'kind': 'NON_NULL', 'ofType': {'kind': 'SCALAR', 'name': 'Int'}}},
    ]}))
    assert 'atnKey' in lines and 'String! ' in lines and '필수' in lines
    assert 'transactionAmount' in lines and '선택' in lines
    assert '기본값=1' in lines          # 기본값이 있으면 안 보내도 된다
