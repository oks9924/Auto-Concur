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

DIFFERENT = Res({'data': {'createDifferentRoomRateItemizations': {
    'status': {'success': True}, 'entry': {'id': 'E1'},
    'entryExceptions': {'countOfExceptions': 0, 'hasBlockingExceptions': False,
                        'entryExceptions': []}}}})

MADE = Res({'data': {'createSameRoomRateItemizations': {
    'status': {'success': True}, 'entry': {'id': 'E1'},
    'entryExceptions': {'countOfExceptions': 0, 'hasBlockingExceptions': False,
                        'entryExceptions': []}}}})


def writes(page):
    return [kw for kw in page.sent if kw.get('data', {}).get('variables', {}).get('skip') is False]


# --- mutation 자체 -----------------------------------------------------------

def test_요금이_같으면_가격_동일_작전():
    page = Page([IDENTIFIED, Res({'data': {}}), MADE])
    gq.itemize(page, 'E1', [150000, 150000, 150000])
    body = writes(page)[0]['data']
    assert body['operationName'] == 'CreateSameRoomRateMutation'
    assert body['variables']['roomRateObject'] == {'roomRate': 150000}
    assert 'roomRateExpenseTypeId' not in body['variables']


def test_요금이_다르면_가격_다름_작전():
    """20만원 3박이면 [66667, 66667, 66666] 이다. 흔한 경우다."""
    page = Page([IDENTIFIED, Res({'data': {}}), DIFFERENT])
    gq.itemize(page, 'E1', [66667, 66667, 66666])
    body = writes(page)[0]['data']
    assert body['operationName'] == 'CreateDifferentRoomRateMutation'
    assert body['variables']['roomRateObject'] == [
        {'roomRate': 66667}, {'roomRate': 66667}, {'roomRate': 66666}]


def test_가격_다름은_합이_경비_총액과_맞는다():
    """'가격 동일' 로 보내면 66667 x 3 = 200,001 로 1원이 넘친다. 그래서 갈랐다."""
    from src.sheet import nightly_split
    nightly = nightly_split(200000, 3)
    page = Page([IDENTIFIED, Res({'data': {}}), DIFFERENT])
    gq.itemize(page, 'E1', nightly)
    rates = [x['roomRate'] for x in writes(page)[0]['data']['variables']['roomRateObject']]
    assert sum(rates) == 200000


def test_보내기_전에_검증한다():
    page = Page([IDENTIFIED, Res({'data': {}}), MADE])
    gq.itemize(page, 'E1', [150000])
    dry = [kw for kw in page.sent if kw.get('data', {}).get('variables', {}).get('skip') is True]
    assert len(dry) == 1 and len(writes(page)) == 1


def test_Concur가_실패라고_하면_성공으로_치지_않는다():
    page = Page([IDENTIFIED, Res({'data': {}}), Res({'data': {
        'createSameRoomRateItemizations': {'status': {'success': False}}}})])
    with pytest.raises(gq.MaybeSent, match='실패로 표시'):
        gq.itemize(page, 'E1', [150000])


def test_요금이_0이면_보내지_않는다():
    page = Page([])
    with pytest.raises(gq.NotSent):
        gq.itemize(page, 'E1', [0])
    assert page.sent == []


# --- 언제 쓰고 언제 안 쓰나 ---------------------------------------------------

def plan_of(amount=300000, nights=2):
    row = type('Row', (), {'expense_id': 'E1', 'amount': amount, 'when': date(2026, 9, 7)})()
    lodging = type('Lodging', (), {
        'checkin': date(2026, 9, 7), 'checkout': date(2026, 9, 7 + nights),
        'nights': nights, 'location': '', 'channel': ''})()
    return type('Plan', (), {'row': row, 'lodging': lodging})()


def wire(monkeypatch, *, itemized, made=None):
    monkeypatch.setattr(gq, 'itemized', lambda page, eid: itemized)
    monkeypatch.setattr(gq, 'lodging_fields',
                        lambda page, eid, *a, **k: {'hotelCheckinDate': '2026-09-07'})
    monkeypatch.setattr(gq, 'save_fields',
                        lambda page, eid, fields: gq.Saved(eid, False, (), ()))
    calls = []

    def fake(page, eid, nightly):
        calls.append((eid, list(nightly)))
        if made is not None:
            raise made
        return gq.Saved(eid, False, (), ())

    monkeypatch.setattr(gq, 'itemize', fake)
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: None)
    return calls


def test_요금이_같으면_GraphQL로_만든다(monkeypatch):
    calls = wire(monkeypatch, itemized=False)
    done = []
    assert fx._lodging_by_api(Page([]), plan_of(), [150000, 150000], True, 'u', done) is True
    assert calls == [('E1', [150000, 150000])]
    note = ' / '.join(done)
    assert '항목별 명세 2건' in note and '150,000원' in note
    assert '숙박 9/7~9/9 (2박)' in note      # 날짜도 같이 들어간다


def test_요금이_달라도_GraphQL로_만든다(monkeypatch):
    """전에는 화면으로 넘겼다. 이제 '가격 다름' 작전이 있다."""
    calls = wire(monkeypatch, itemized=False)
    done = []
    assert fx._lodging_by_api(Page([]), plan_of(200000),
                                [66667, 66667, 66666], True, 'u', done) is True
    assert calls == [('E1', [66667, 66667, 66666])]
    assert '66,666~66,667원' in ' / '.join(done)


def test_이미_명세가_있으면_안_만든다(monkeypatch):
    calls = wire(monkeypatch, itemized=True)
    assert fx._lodging_by_api(Page([]), plan_of(), [150000, 150000], True, 'u', []) is False
    assert calls == []


def test_있는지_모르면_안_만든다(monkeypatch):
    """모르면 만들지 않는다. 중복 명세는 되돌리기 어렵다."""
    calls = wire(monkeypatch, itemized=None)
    assert fx._lodging_by_api(Page([]), plan_of(), [150000, 150000], True, 'u', []) is False
    assert calls == []


def test_보내기_전_실패면_화면으로_간다(monkeypatch, capsys):
    calls = wire(monkeypatch, itemized=False, made=gq.NotSent('세션 모름'))
    assert fx._lodging_by_api(Page([]), plan_of(), [150000], True, 'u', []) is False
    assert '화면으로 갑니다' in capsys.readouterr().out


def test_보낸_뒤_실패면_화면으로_안_간다(monkeypatch):
    """이중 명세를 막는다. 올려보내서 그 경비만 실패로 남긴다."""
    wire(monkeypatch, itemized=False, made=gq.MaybeSent('결과 불명'))
    with pytest.raises(gq.MaybeSent):
        fx._lodging_by_api(Page([]), plan_of(), [150000], True, 'u', [])


def test_명세_전에_상세를_저장한다(monkeypatch):
    """날짜 범위·위치를 화면에서 넣어둔 상태다. 저장 안 하면 그게 날아간다."""
    wire(monkeypatch, itemized=False)
    saved = []
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: saved.append(1))
    fx._lodging_by_api(Page([]), plan_of(), [150000], True, 'u', [])
    assert saved == [1]


def test_바꾼_것이_없으면_저장하지_않는다(monkeypatch):
    wire(monkeypatch, itemized=False)
    saved = []
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: saved.append(1))
    fx._lodging_by_api(Page([]), plan_of(), [150000], False, 'u', [])
    assert saved == []


def test_실제_분배가_그대로_간다(monkeypatch):
    """합은 늘 경비 총액과 맞아야 한다. 같든 다르든."""
    from src.sheet import nightly_split
    for total, nights in ((300000, 3), (200000, 3), (17000, 3), (450000, 3), (100000, 2)):
        nightly = nightly_split(total, nights)
        calls = wire(monkeypatch, itemized=False)
        assert fx._lodging_by_api(Page([]), plan_of(total), nightly, True, 'u', []) is True
        assert calls == [('E1', nightly)]
        assert sum(calls[0][1]) == total


# --- 숙박 상세 (날짜·위치·채널) ---------------------------------------------

def test_날짜와_목록_칸을_한_번에_보낸다(monkeypatch):
    """화면의 날짜 범위 입력칸과 콤보박스 둘이 이 한 호출로 대체된다."""
    form = [
        {'id': 'custom10', 'label': '숙박 위치',
         'options': [{'id': 'L1', 'code': 'D', 'value': '국내'},
                     {'id': 'L2', 'code': 'O', 'value': '해외'}]},
        {'id': 'custom16', 'label': 'Booking channel',
         'options': [{'id': 'C1', 'code': 'A', 'value': '직접예약'}]},
    ]
    monkeypatch.setattr(gq, 'existing_form_fields', lambda page, eid: form)
    got = gq.lodging_fields(Page([]), 'E1', date(2026, 9, 7), date(2026, 9, 9),
                            '국내', '직접예약')
    assert got['hotelCheckinDate'] == '2026-09-07'
    assert got['hotelCheckoutDate'] == '2026-09-09'
    assert got['custom10'] == {'listItemId': 'L1', 'value': 'L1'}
    assert got['custom16'] == {'listItemId': 'C1', 'value': 'C1'}


def test_날짜만_있으면_폼을_읽지도_않는다(monkeypatch):
    monkeypatch.setattr(gq, 'existing_form_fields',
                        lambda page, eid: pytest.fail('폼을 읽었습니다'))
    got = gq.lodging_fields(Page([]), 'E1', date(2026, 9, 7), date(2026, 9, 9))
    assert set(got) == {'hotelCheckinDate', 'hotelCheckoutDate'}


def test_목록에서_못_고르면_반만_넣지_않는다(monkeypatch):
    form = [{'id': 'custom10', 'label': '숙박 위치',
             'options': [{'id': 'L1', 'code': 'D', 'value': '국내'}]}]
    monkeypatch.setattr(gq, 'existing_form_fields', lambda page, eid: form)
    with pytest.raises(gq.NotSent, match='유일하게 고르지 못했습니다'):
        gq.lodging_fields(Page([]), 'E1', date(2026, 9, 7), date(2026, 9, 9), '없는위치', '')


def test_넣을_것이_없으면_멈춘다():
    with pytest.raises(gq.NotSent, match='넣을 값이 없습니다'):
        gq.lodging_fields(Page([]), 'E1', None, None, '', '')


def test_숙박_날짜만_있고_명세가_없어도_된다(monkeypatch):
    """입실·퇴실이 비면 명세는 안 만들고 위치·채널만 넣는다."""
    calls = wire(monkeypatch, itemized=False)
    plan = plan_of(nights=0)
    plan.lodging.location = '국내'
    done = []
    assert fx._lodging_by_api(Page([]), plan, [], True, 'u', done) is True
    assert calls == []                       # 명세는 안 만든다
    assert '숙박 위치' in ' / '.join(done)


def test_어떤_예외도_화면_경로를_막지_않는다(capsys):
    """세 번 틀린 자리다 - fill_gaps, 마일리지 생성, 그리고 여기."""
    from unittest.mock import Mock
    plan = plan_of()
    assert fx._lodging_by_api(Mock(), plan, [], True, 'u', []) is False
    assert '화면으로 갑니다' in capsys.readouterr().out


def test_보낸_뒤_모르면_화면으로_안_간다(monkeypatch):
    """이중 저장을 막는다."""
    monkeypatch.setattr(gq, 'itemized', lambda page, eid: False)
    monkeypatch.setattr(gq, 'lodging_fields',
                        lambda *a, **k: {'hotelCheckinDate': '2026-09-07'})
    monkeypatch.setattr(fx, '_save_expense', lambda *a, **k: None)
    monkeypatch.setattr(gq, 'save_fields',
                        lambda *a, **k: (_ for _ in ()).throw(gq.MaybeSent('결과 불명')))
    with pytest.raises(gq.MaybeSent):
        fx._lodging_by_api(Page([]), plan_of(), [], True, 'u', [])
