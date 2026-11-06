"""마일리지 경비 생성. 이건 '고치기' 가 아니라 '만들기' 다.

틀리면 없던 재무 기록이 생기고 사람이 찾아 지워야 한다. 그래서 이 파일의 본문은
"하나라도 못 채우면 아무것도 보내지 않는다" 와 "금액은 우리가 계산하지 않는다" 다.
"""
from datetime import date

import pytest

from src import concur_graphql as gq


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
        sent = self.sent = []

        class Req:
            @staticmethod
            def post(url, **kw):
                sent.append(kw)
                return replies.pop(0) if replies else Res({'data': {}})

        self.context = type('C', (), {'request': Req()})()


def listed(**kw):
    """폼 칸 하나."""
    one = {'id': kw.pop('id'), 'label': kw.pop('label', ''),
           'requiredForSave': kw.pop('required', False),
           'options': kw.pop('options', []), 'value': kw.pop('value', {}),
           'defaultValue': kw.pop('default', {})}
    one.update(kw)
    return one


VEHICLE = listed(id='vehicleId', label='차량 ID', required=True,
                 options=[{'id': '11', 'value': '12가3456'},
                          {'id': '22', 'value': '34나5678'}])
COUNTRY = listed(id='countryCode', required=True,
                 value={'listValue': {'id': 'KR', 'code': 'KR', 'value': '대한민국'}})
PAYMENT = listed(id='paymentTypeId', required=True,
                 value={'listValue': {'id': 'CASH', 'value': '현금'}})
RATE = listed(id='exchangeRate', required=True,
              value={'operation': 'MULTIPLY', 'value': 1})
OPTIONAL = listed(id='comment', required=False)

FORM = [VEHICLE, COUNTRY, PAYMENT, RATE, OPTIONAL]


def wire(monkeypatch, *, form=None, made=None):
    monkeypatch.setattr(gq, 'ids', lambda page: ('R1', 'U1', 'TRAVELER'))
    monkeypatch.setattr(gq, 'expense_type_id', lambda *a: 'MILEG')
    monkeypatch.setattr(gq, 'new_form_fields', lambda *a: form if form is not None else FORM)


def made_ok(amount=12500):
    return Res({'data': {'createExpense': {
        'id': 'NEW1',
        'entry': {'id': 'NEW1', 'transactionAmount': {'value': amount, 'currencyCode': 'KRW'}},
        'entryExceptions': {'countOfExceptions': 0, 'hasBlockingExceptions': False,
                            'entryExceptions': []}}}})


def writes(page):
    return [kw for kw in page.sent if kw.get('data', {}).get('variables', {}).get('skip') is False]


def run(page, **kw):
    args = dict(type_name='자동차 마일리지', vehicle='12가3456', when=date(2026, 9, 7),
                origin='서울', destination='부산', distance=400)
    args.update(kw)
    return gq.create_mileage(page, **args)


# --- 보내는 내용 ------------------------------------------------------------

def test_차량을_표시_이름으로_고른다(monkeypatch):
    wire(monkeypatch)
    page = Page([Res({'data': {}}), made_ok()])
    run(page)
    fields = writes(page)[0]['data']['variables']['fields']
    assert fields['vehicleId'] == '11'          # '12가3456' -> id 11
    assert fields['transactionDate'] == '2026-09-07'
    assert fields['startLocation'] == '서울' and fields['endLocation'] == '부산'
    assert fields['totalDistance'] == 400


def test_폼이_필요하다고_한_칸은_폼_값을_쓴다(monkeypatch):
    wire(monkeypatch)
    page = Page([Res({'data': {}}), made_ok()])
    run(page)
    fields = writes(page)[0]['data']['variables']['fields']
    assert fields['countryCode'] == 'KR'
    assert fields['paymentTypeId'] == 'CASH'
    assert fields['exchangeRate'] == {'operation': 'MULTIPLY', 'value': 1}


def test_필수가_아닌_칸은_보내지_않는다(monkeypatch):
    wire(monkeypatch)
    page = Page([Res({'data': {}}), made_ok()])
    run(page)
    assert 'comment' not in writes(page)[0]['data']['variables']['fields']


def test_금액은_우리가_계산하지_않는다(monkeypatch):
    """거리 x 정책 요율이다. 우리가 보내면 회사 요율을 덮어쓸 수 있다."""
    wire(monkeypatch)
    page = Page([Res({'data': {}}), made_ok()])
    run(page)
    assert 'transactionAmount' not in writes(page)[0]['data']['variables']['fields']


def test_금액이_계산되지_않으면_알린다(monkeypatch):
    wire(monkeypatch)
    page = Page([Res({'data': {}}), made_ok(amount=0)])
    saved = run(page)
    assert any('금액이 계산되지 않았' in m for m in saved.messages)


def test_보내기_전에_검증한다(monkeypatch):
    wire(monkeypatch)
    page = Page([Res({'data': {}}), made_ok()])
    run(page)
    dry = [kw for kw in page.sent if kw.get('data', {}).get('variables', {}).get('skip') is True]
    assert len(dry) == 1 and len(writes(page)) == 1


# --- 못 채우면 아무것도 안 보낸다 --------------------------------------------

def test_필수_칸을_못_채우면_아무것도_안_보낸다(monkeypatch):
    """반쯤 만든 경비를 남기지 않는다. 지우는 건 사람 몫이 된다."""
    wire(monkeypatch, form=FORM + [listed(id='custom13', label='프로젝트', required=True)])
    page = Page([])
    with pytest.raises(gq.NotSent, match='필수 칸을 채우지 못했습니다'):
        run(page)
    assert page.sent == []


def test_차량을_못_고르면_아무것도_안_보낸다(monkeypatch):
    wire(monkeypatch)
    page = Page([])
    with pytest.raises(gq.NotSent, match='유일하게 고르지 못했습니다'):
        run(page, vehicle='없는차량')
    assert page.sent == []


def test_폼에_차량_칸이_없으면_멈춘다(monkeypatch):
    wire(monkeypatch, form=[COUNTRY])
    page = Page([])
    with pytest.raises(gq.NotSent, match='vehicleId 칸이 없습니다'):
        run(page)


def test_생성_응답에_id_가_없으면_성공으로_치지_않는다(monkeypatch):
    wire(monkeypatch)
    page = Page([Res({'data': {}}), Res({'data': {'createExpense': {}}})])
    with pytest.raises(gq.MaybeSent, match='createExpense.id'):
        run(page)


# --- 경비유형 고르기 --------------------------------------------------------

def test_경비유형은_유일할_때만_고른다(monkeypatch):
    monkeypatch.setattr(gq, 'policy_id', lambda *a: 'P1')
    page = Page([Res({'data': {'expenseTypesForReport': [
        {'id': 'M1', 'code': 'MILEG', 'name': '자동차 마일리지', 'text': '자동차 마일리지'},
        {'id': 'M2', 'code': 'MILE2', 'name': '회사차 마일리지', 'text': '회사차 마일리지'}]}})])
    assert gq.expense_type_id(page, '자동차 마일리지', 'R1', 'U1', 'TRAVELER') == 'M1'


def test_경비유형이_애매하면_멈춘다(monkeypatch):
    monkeypatch.setattr(gq, 'policy_id', lambda *a: 'P1')
    page = Page([Res({'data': {'expenseTypesForReport': [
        {'id': 'M1', 'code': 'A', 'name': '마일리지', 'text': '마일리지'},
        {'id': 'M2', 'code': 'B', 'name': '마일리지', 'text': '마일리지'}]}})])
    with pytest.raises(gq.NotSent, match='유일하게 고르지 못했습니다'):
        gq.expense_type_id(page, '마일리지', 'R1', 'U1', 'TRAVELER')


# --- 마일리지 흐름에 끼워진 자리 --------------------------------------------

def test_만들면_화면_경로로_안_간다(monkeypatch):
    from src import mileage_concur as mc
    monkeypatch.setattr(mc, 'check_context', lambda *a: None)
    monkeypatch.setattr(gq, 'create_mileage',
                        lambda page, *a, **k: gq.Saved('NEW1', False, (), ()))
    monkeypatch.setattr(mc.ui, 'click_target',
                        lambda *a, **k: pytest.fail('화면 경로로 갔습니다'))
    page = type('P', (), {'goto': lambda s, *a, **k: None})()
    row = {'vehicle': '12가3456', 'date': date(2026, 9, 7), 'origin': '서울',
           'destination': '부산', 'distance': 400, 'passengers': 1, 'description': ''}
    assert mc.create_one(page, 'https://eu2.concursolutions.com/nui/expense/reports/R1',
                         row, None) == 'NEW1'


def test_못_만들면_화면_경로로_간다(monkeypatch):
    """NotSent 다 - 아무것도 안 만들었으므로 화면으로 넘어가도 안전하다."""
    from src import mileage_concur as mc
    monkeypatch.setattr(mc, 'check_context', lambda *a: None)
    monkeypatch.setattr(gq, 'create_mileage',
                        lambda page, *a, **k: (_ for _ in ()).throw(gq.NotSent('폼 못 읽음')))
    went = []
    monkeypatch.setattr(mc, '_report_ids', lambda page: set())
    monkeypatch.setattr(mc.ui, 'click_target', lambda *a, **k: went.append(1))
    monkeypatch.setattr(mc, '_click_unique_text',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('여기까지')))
    page = type('P', (), {'goto': lambda s, *a, **k: None, 'wait_for_timeout': lambda s, n: None})()
    row = {'vehicle': 'x', 'date': date(2026, 9, 7), 'origin': 'a',
           'destination': 'b', 'distance': 1, 'passengers': 1, 'description': ''}
    with pytest.raises(RuntimeError, match='여기까지'):
        mc.create_one(page, 'https://eu2.concursolutions.com/nui/expense/reports/R1', row, None)
    assert went, '화면 경로가 시작되지 않았습니다'


def test_지도가_실패해도_경비는_남는다(monkeypatch, capsys):
    from src import mileage_concur as mc
    monkeypatch.setattr(mc, 'check_context', lambda *a: None)
    monkeypatch.setattr(gq, 'create_mileage',
                        lambda page, *a, **k: gq.Saved('NEW1', False, (), ()))
    monkeypatch.setattr(gq, 'attach_receipt',
                        lambda *a, **k: (_ for _ in ()).throw(gq.NotSent('파일 없음')))
    page = type('P', (), {'goto': lambda s, *a, **k: None})()
    row = {'vehicle': 'x', 'date': date(2026, 9, 7), 'origin': 'a',
           'destination': 'b', 'distance': 1, 'passengers': 1, 'description': ''}
    assert mc._create_by_api(page, 'https://eu2.concursolutions.com/nui/expense/reports/R1',
                             row, 'map.png') == 'NEW1'
    assert '지도 첨부는 못 했습니다' in capsys.readouterr().out


def test_어떤_예외도_화면_경로를_막지_않는다(capsys):
    """page 가 예상한 모양이 아니어도 지금까지 되던 것은 그대로 돼야 한다."""
    from src import mileage_concur as mc
    row = {'vehicle': 'x', 'date': date(2026, 9, 7), 'origin': 'a',
           'destination': 'b', 'distance': 1}
    assert mc._create_by_api(object(), 'https://eu2.concursolutions.com/x', row, None) is None
    assert '화면으로 만듭니다' in capsys.readouterr().out


def test_보낸_뒤_모르면_화면으로_또_만들지_않는다(monkeypatch):
    """경비가 둘이 된다. 지우는 건 사람 몫이다."""
    from src import mileage_concur as mc
    monkeypatch.setattr(mc, 'check_context', lambda *a: None)
    monkeypatch.setattr(gq, 'create_mileage',
                        lambda page, *a, **k: (_ for _ in ()).throw(gq.MaybeSent('결과 불명')))
    page = type('P', (), {'goto': lambda s, *a, **k: None})()
    row = {'vehicle': 'x', 'date': date(2026, 9, 7), 'origin': 'a',
           'destination': 'b', 'distance': 1}
    with pytest.raises(gq.MaybeSent):
        mc._create_by_api(page, 'https://eu2.concursolutions.com/x', row, None)


def test_경비유형_이름이_조금_달라도_포함으로_찾는다(monkeypatch):
    """2026-11-06 실측: '자동차 마일리지' 가 40개 중 정확히 맞는 것이 없었다."""
    monkeypatch.setattr(gq, 'policy_id', lambda *a: 'P1')
    page = Page([Res({'data': {'expenseTypesForReport': [
        {'id': 'M1', 'code': 'CARML', 'name': '자동차 마일리지 (개인차량)',
         'text': '자동차 마일리지 (개인차량)'},
        {'id': 'X1', 'code': 'MEAL', 'name': '식음료', 'text': '식음료'}]}})])
    assert gq.expense_type_id(page, '자동차 마일리지', 'R1', 'U1', 'TRAVELER') == 'M1'


def test_포함으로도_여럿이면_후보를_알려준다(monkeypatch):
    monkeypatch.setattr(gq, 'policy_id', lambda *a: 'P1')
    page = Page([Res({'data': {'expenseTypesForReport': [
        {'id': 'M1', 'code': 'A', 'name': '자동차 마일리지 (개인)', 'text': '자동차 마일리지 (개인)'},
        {'id': 'M2', 'code': 'B', 'name': '자동차 마일리지 (법인)', 'text': '자동차 마일리지 (법인)'}]}})])
    with pytest.raises(gq.NotSent, match='후보:'):
        gq.expense_type_id(page, '자동차 마일리지', 'R1', 'U1', 'TRAVELER')
