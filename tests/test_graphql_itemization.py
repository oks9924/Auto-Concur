"""숙박 '가격 동일' 명세를 GraphQL로 만든다.

이 mutation 은 명세를 **만든다**. 이미 있는데 또 만들면 중복이다. 그래서
"모르면 만들지 않는다" 가 이 파일의 본문이다.
"""
from datetime import date

import pytest

from src import concur_graphql as gq, fix_expenses as fx


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    gq.forget()
    gq._TOLD.clear()
    monkeypatch.delenv('AUTO_CONCUR_NO_GRAPHQL', raising=False)


class Res:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        return self._body

    def text(self):
        return ''


class Page:
    url = 'https://eu2.concursolutions.com/nui/expense/reports/R1/expenses'

    def __init__(self, replies):
        sent = []
        self.sent = sent

        class Req:
            @staticmethod
            def post(url, **kw):
                sent.append(kw)
                return replies.pop(0) if replies else Res({'data': {}})

        self.context = type('C', (), {'request': Req()})()


IDENTIFIED = Res({'data': {'employee': {
    'userId': 'U1', 'contextRole': 'TRAVELER',
    'expenseReport': {'reportId': 'R1', 'rptKey': 'K1'}}}})

MADE = Res({'data': {'createSameRoomRateItemizations': {
    'status': {'success': True}, 'entry': {'id': 'E1'},
    'entryExceptions': {'countOfExceptions': 0, 'hasBlockingExceptions': False,
                        'entryExceptions': []}}}})


def writes(page):
    return [kw for kw in page.sent if kw.get('data', {}).get('variables', {}).get('skip') is False]


# --- mutation 자체 -----------------------------------------------------------

def test_1박_요금_하나만_보낸다():
    page = Page([IDENTIFIED, Res({'data': {}}), MADE])
    gq.same_room_rate(page, 'E1', 150000)
    body = writes(page)[0]['data']
    assert body['operationName'] == 'CreateSameRoomRateMutation'
    assert body['variables']['roomRateObject'] == {'roomRate': 150000}
    assert 'roomRateExpenseTypeId' not in body['variables']


def test_보내기_전에_검증한다():
    page = Page([IDENTIFIED, Res({'data': {}}), MADE])
    gq.same_room_rate(page, 'E1', 150000)
    dry = [kw for kw in page.sent if kw.get('data', {}).get('variables', {}).get('skip') is True]
    assert len(dry) == 1 and len(writes(page)) == 1


def test_Concur가_실패라고_하면_성공으로_치지_않는다():
    page = Page([IDENTIFIED, Res({'data': {}}), Res({'data': {
        'createSameRoomRateItemizations': {'status': {'success': False}}}})])
    with pytest.raises(gq.MaybeSent, match='실패로 표시'):
        gq.same_room_rate(page, 'E1', 150000)


def test_요금이_0이면_보내지_않는다():
    page = Page([])
    with pytest.raises(gq.NotSent):
        gq.same_room_rate(page, 'E1', 0)
    assert page.sent == []


# --- 언제 쓰고 언제 안 쓰나 ---------------------------------------------------

def plan_of(amount=300000):
    row = type('Row', (), {'expense_id': 'E1', 'amount': amount, 'when': date(2026, 9, 7)})()
    return type('Plan', (), {'row': row})()


def wire(monkeypatch, *, itemized, made=None):
    monkeypatch.setattr(gq, 'itemized', lambda page, eid: itemized)
    calls = []

    def fake(page, eid, rate):
        calls.append((eid, rate))
        if made is not None:
            raise made
        return gq.Saved(eid, False, (), ())

    monkeypatch.setattr(gq, 'same_room_rate', fake)
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: None)
    return calls


def test_요금이_같으면_GraphQL로_만든다(monkeypatch):
    calls = wire(monkeypatch, itemized=False)
    done = []
    assert fx._same_rate_by_api(Page([]), plan_of(), [150000, 150000], True, 'u', done) is True
    assert calls == [('E1', 150000)]
    assert '항목별 명세 2건' in done[0] and '150,000원' in done[0]


def test_요금이_다르면_화면으로_간다(monkeypatch):
    """'가격 동일' 이 작전 이름에 박혀 있다. 다르면 이 길이 아니다."""
    calls = wire(monkeypatch, itemized=False)
    assert fx._same_rate_by_api(Page([]), plan_of(), [150000, 149999], True, 'u', []) is False
    assert calls == []


def test_이미_명세가_있으면_안_만든다(monkeypatch):
    calls = wire(monkeypatch, itemized=True)
    assert fx._same_rate_by_api(Page([]), plan_of(), [150000, 150000], True, 'u', []) is False
    assert calls == []


def test_있는지_모르면_안_만든다(monkeypatch):
    """모르면 만들지 않는다. 중복 명세는 되돌리기 어렵다."""
    calls = wire(monkeypatch, itemized=None)
    assert fx._same_rate_by_api(Page([]), plan_of(), [150000, 150000], True, 'u', []) is False
    assert calls == []


def test_보내기_전_실패면_화면으로_간다(monkeypatch, capsys):
    calls = wire(monkeypatch, itemized=False, made=gq.NotSent('세션 모름'))
    assert fx._same_rate_by_api(Page([]), plan_of(), [150000], True, 'u', []) is False
    assert '화면으로 갑니다' in capsys.readouterr().out


def test_보낸_뒤_실패면_화면으로_안_간다(monkeypatch):
    """이중 명세를 막는다. 올려보내서 그 경비만 실패로 남긴다."""
    wire(monkeypatch, itemized=False, made=gq.MaybeSent('결과 불명'))
    with pytest.raises(gq.MaybeSent):
        fx._same_rate_by_api(Page([]), plan_of(), [150000], True, 'u', [])


def test_명세_전에_상세를_저장한다(monkeypatch):
    """날짜 범위·위치를 화면에서 넣어둔 상태다. 저장 안 하면 그게 날아간다."""
    wire(monkeypatch, itemized=False)
    saved = []
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: saved.append(1))
    fx._same_rate_by_api(Page([]), plan_of(), [150000], True, 'u', [])
    assert saved == [1]


def test_바꾼_것이_없으면_저장하지_않는다(monkeypatch):
    wire(monkeypatch, itemized=False)
    saved = []
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: saved.append(1))
    fx._same_rate_by_api(Page([]), plan_of(), [150000], False, 'u', [])
    assert saved == []


def test_끄면_화면으로_간다(monkeypatch):
    monkeypatch.setenv('AUTO_CONCUR_NO_GRAPHQL', '1')
    calls = wire(monkeypatch, itemized=False)
    assert fx._same_rate_by_api(Page([]), plan_of(), [150000], True, 'u', []) is False
    assert calls == []


def test_요금이_다르면_왜_화면으로_갔는지_말한다(monkeypatch, capsys):
    """흔한 경우다. 20만원 3박이면 [66667, 66667, 66666] 이다.
    조용히 넘어가면 '이관이 안 되네' 로 보인다."""
    wire(monkeypatch, itemized=False)
    assert fx._same_rate_by_api(Page([]), plan_of(200000),
                                [66667, 66667, 66666], True, 'u', []) is False
    assert '1박 요금이 달라' in capsys.readouterr().out


def test_실제_분배가_같은지_다른지로_갈린다(monkeypatch):
    """합은 늘 맞지만 요금이 같은 경우와 다른 경우가 섞여 있다."""
    from src.sheet import nightly_split
    for total, nights, expect in ((300000, 3, True), (200000, 3, False),
                                  (450000, 3, True), (17000, 3, False)):
        amounts = nightly_split(total, nights)
        assert sum(amounts) == total
        calls = wire(monkeypatch, itemized=False)
        used = fx._same_rate_by_api(Page([]), plan_of(total), amounts, True, 'u', [])
        assert used is expect, (total, nights, amounts)
        assert bool(calls) is expect
