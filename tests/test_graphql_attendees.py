"""참석자를 GraphQL로 저장한다. 전에 실제로 데이터를 날린 자리다.

`saveAttendeesLegacy` 는 목록을 **통째로 교체한다.** 그게 그 사고의 메커니즘이었다.
그래서 이 파일의 본문은 "한 명이라도 유일하게 못 고르면 아무것도 보내지 않는다" 다.
"""
import pytest

from src import concur_graphql as gq
from src.concur_driver import Driver


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    gq.forget()
    gq._TOLD.clear()
    monkeypatch.delenv('AUTO_CONCUR_NO_GRAPHQL', raising=False)


def person(key, first, last, mail, version=3, code='EMPL'):
    return {'atnKey': key, 'atnTypeCode': code, 'versionNumber': version,
            'firstName': first, 'lastName': last, 'preferredName': f'{last} {first}',
            'emailAddress': mail}


OH = person('K1', 'Kyungsik', 'Oh', 'kyungsik.oh@siemens.com')
HONG = person('K2', 'Gildong', 'Hong', 'hong.gildong@siemens.com')
OTHER_OH = person('K3', 'Kyungsoo', 'Oh', 'kyungsoo.oh@siemens.com')


# --- 고르기 ----------------------------------------------------------------

def test_메일_주소로_정확히_고른다():
    """'kyungsik.oh' 가 'Oh Kyungsik' 과 토막 대조로는 안 맞던 자리다."""
    assert gq.pick_attendee('kyungsik.oh', [OH, HONG, OTHER_OH]) is OH
    assert gq.pick_attendee('kyungsik.oh@siemens.com', [OH, HONG]) is OH


def test_도메인이_달라도_계정_이름으로_맞춘다():
    assert gq.pick_attendee('kyungsik.oh@other.com', [OH]) is OH


def test_이름으로도_고른다():
    assert gq.pick_attendee('Oh Kyungsik', [OH, HONG]) is OH


def test_여럿이면_고르지_않는다():
    """두 명이 맞으면 누구인지 모른다. 추측하면 엉뚱한 사람이 경비에 붙는다."""
    assert gq.pick_attendee('Oh', [OH, OTHER_OH]) is None


def test_아무도_안_맞으면_None():
    assert gq.pick_attendee('없는사람', [OH, HONG]) is None


# --- 저장 ------------------------------------------------------------------

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
        sent = self.sent = []

        class Req:
            @staticmethod
            def post(url, **kw):
                sent.append((url, kw))
                return replies.pop(0) if replies else Res({'data': {}})

        self.context = type('C', (), {'request': Req()})()


IDENTIFIED = Res({'data': {'employee': {
    'userId': 'U1', 'contextRole': 'TRAVELER',
    'expenseReport': {'reportId': 'R1', 'rptKey': 'K1'}}}})


def writes(page):
    return [kw for _, kw in page.sent
            if kw.get('data', {}).get('variables', {}).get('skip') is False]


def test_버전_번호를_같이_보낸다():
    """낙관적 동시성이다. 남이 먼저 바꿨으면 우리가 덮지 않게 한다."""
    page = Page([IDENTIFIED, Res({'data': {}}), Res({'data': {}})])
    gq.save_attendees(page, 'E1', [OH, HONG], [8500, 8500])
    sent = writes(page)[0]['data']['variables']['params']['attendees']
    assert [a['versionNumber'] for a in sent] == [3, 3]
    assert [a['atnKey'] for a in sent] == ['K1', 'K2']
    assert [a['transactionAmount'] for a in sent] == [8500, 8500]
    assert all(a['instanceCount'] == 1 for a in sent)


def test_보내기_전에_검증한다():
    page = Page([IDENTIFIED, Res({'data': {}}), Res({'data': {}})])
    gq.save_attendees(page, 'E1', [OH], [1000])
    dry = [kw for _, kw in page.sent
           if kw.get('data', {}).get('variables', {}).get('skip') is True]
    assert len(dry) == 1 and len(writes(page)) == 1


def test_참석자가_없으면_아무것도_보내지_않는다():
    page = Page([])
    with pytest.raises(gq.NotSent):
        gq.save_attendees(page, 'E1', [], [])
    assert page.sent == []


def test_유형_키가_비면_멈춘다():
    """빈 목록으로 검색하면 후보가 안 나와서 참석자를 못 찾는다. 조용히 넘기지 않는다."""
    page = Page([Res({'attendee_GetAttendeeTypes': {'records': []}})])
    with pytest.raises(gq.NotSent, match='forEmpKey'):
        gq.attendee_type_keys(page, 'U1')


def test_구형_라우터는_실측_헤더로_부른다():
    page = Page([Res({'attendee_GetAttendeeTypes': {'records': [
        {'atnTypeKey': 11, 'isDeleted': 'N'}, {'atnTypeKey': 22, 'isDeleted': 'Y'}]}})])
    assert gq.attendee_type_keys(page, 'U1') == [11]     # 지워진 유형은 뺀다
    url, kw = page.sent[0]
    assert url.endswith(gq.LEGACY_PATH)
    assert kw['headers']['camelcase'] == 'Y' and kw['headers']['fixjson'] == 'Y'
    assert kw['data'] == {'action': 'Attendee', 'method': 'GetAttendeeTypes',
                          'data': {'forEmpKey': 'U1'}}


# --- 드라이버: 한 명이라도 못 고르면 아무것도 안 보낸다 ----------------------

def driver(monkeypatch, found_for, amount=17000):
    from src.attach_receipts import Row
    from src.fix_expenses import Plan
    sent = []
    monkeypatch.setattr(gq, 'ids', lambda page: ('R1', 'U1', 'TRAVELER'))
    monkeypatch.setattr(gq, 'attendee_type_keys', lambda page, uid: [11, 22])
    monkeypatch.setattr(gq, 'search_attendees',
                        lambda page, terms, keys: found_for.get(terms, []))
    monkeypatch.setattr(gq, 'save_attendees',
                        lambda page, eid, chosen, amounts: sent.append((chosen, amounts)))
    row = Row(index=0, when=None, amount=amount, text='', expense_id='E1')
    d = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    d.guard = lambda: None
    return d, row, sent, Plan


def test_둘_다_고르면_둘을_보낸다(monkeypatch):
    d, row, sent, Plan = driver(monkeypatch, {'kyungsik.oh': [OH], 'hong.gildong': [HONG]})
    plan = Plan(row=row, type_code=None, type_label='식음료',
                attendee='kyungsik.oh@siemens.com, hong.gildong@siemens.com')
    assert d._attendees_by_api(plan) == '참석자 2명'
    chosen, amounts = sent[0]
    assert [a['atnKey'] for a in chosen] == ['K1', 'K2']
    assert sum(amounts) == 17000       # 합이 경비 금액과 맞는다


def test_한_명이라도_애매하면_아무도_안_보낸다(monkeypatch):
    """반쯤 고른 목록을 보내면 나머지가 지워진다. 전에 낸 사고가 그 모양이었다."""
    d, row, sent, Plan = driver(monkeypatch,
                                {'kyungsik.oh': [OH], 'oh': [OH, OTHER_OH]})
    plan = Plan(row=row, type_code=None, type_label='식음료',
                attendee='kyungsik.oh, oh')
    with pytest.raises(gq.NotSent, match='유일하게 고르지 못했습니다'):
        d._attendees_by_api(plan)
    assert sent == []


def test_아무도_못_찾아도_아무도_안_보낸다(monkeypatch):
    d, row, sent, Plan = driver(monkeypatch, {})
    plan = Plan(row=row, type_code=None, type_label='식음료', attendee='없는사람')
    with pytest.raises(gq.NotSent):
        d._attendees_by_api(plan)
    assert sent == []


def test_같은_사람을_두_번_적어도_한_번만(monkeypatch):
    d, row, sent, Plan = driver(monkeypatch,
                                {'kyungsik.oh': [OH], 'Oh Kyungsik': [OH]})
    plan = Plan(row=row, type_code=None, type_label='식음료',
                attendee='kyungsik.oh@siemens.com, Oh Kyungsik')
    assert d._attendees_by_api(plan) == '참석자 1명'
    assert len(sent[0][0]) == 1


def test_참석자가_애매하면_화면_경로로_간다(monkeypatch):
    """NotSent 다 - 아무것도 안 보냈으므로 화면으로 넘어가도 안전하다."""
    d, row, sent, Plan = driver(monkeypatch, {})
    went = []
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: went.append(1) or 'ok')
    plan = Plan(row=row, type_code=None, type_label='식음료', attendee='없는사람')
    d.apply_edit(plan)
    assert went == [1] and sent == []
