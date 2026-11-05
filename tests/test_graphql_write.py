"""저장·첨부 쓰기 경로. 재무 기록에 쓰는 길이라 실패 처리가 본문이다.

핵심 규율 두 개:
  1. 보내기 전에 `@skip(if: true)` 로 문서·변수를 검증한다 (쓰지 않는다).
  2. 보낸 뒤 결과를 모르면 **재시도도 폴백도 하지 않는다** - 둘 다 이중 저장이 된다.
     concur_ui.click_target 이 "클릭 결과를 확인할 수 없으면 재클릭하지 않는다" 로
     이미 같은 판단을 하고 있다.
"""
from pathlib import Path

import pytest

from src import concur_graphql as gq
from src.attach_receipts import Row
from src.concur_driver import Driver
from src.fix_expenses import Plan


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
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class Req:
    """보낸 것을 다 적어둔다. '몇 번 보냈나' 가 이 테스트들의 핵심이다."""
    def __init__(self, replies):
        self.replies, self.sent = replies, []

    def post(self, url, **kw):
        self.sent.append((url, kw))
        reply = self.replies.pop(0) if self.replies else Res({'data': {}})
        if isinstance(reply, Exception):
            raise reply
        return reply


class Ctx:
    def __init__(self, replies):
        self.request = Req(replies)


class Page:
    url = 'https://eu2.concursolutions.com/nui/expense/reports/R1/expenses'

    def __init__(self, replies):
        self.context = Ctx(replies)


IDENTIFIED = Res({'data': {'employee': {
    'userId': 'U1', 'contextRole': 'TRAVELER',
    'expenseReport': {'reportId': 'R1', 'rptKey': 'K1'}}}})

SAVED_CLEAN = Res({'data': {'updateExpense': {
    'id': 'E1', 'entry': {'id': 'E1'},
    'entryExceptions': {'countOfExceptions': 0, 'hasBlockingExceptions': False,
                        'entryExceptions': []}}}})


def writes(page):
    """쓰기 호출만 (skip=false 로 나간 것)."""
    return [kw for _, kw in page.context.request.sent
            if kw.get('data', {}).get('variables', {}).get('skip') is False]


def dry_runs(page):
    return [kw for _, kw in page.context.request.sent
            if kw.get('data', {}).get('variables', {}).get('skip') is True]


# --- 저장 ------------------------------------------------------------------

def test_보내기_전에_검증부터_한다():
    page = Page([IDENTIFIED, Res({'data': {}}), SAVED_CLEAN])
    gq.save_fields(page, 'E1', {'businessPurpose': '회의'})
    assert len(dry_runs(page)) == 1 and len(writes(page)) == 1
    # 검증과 실제 쓰기가 같은 문서·같은 변수여야 의미가 있다
    dry, real = dry_runs(page)[0]['data'], writes(page)[0]['data']
    assert dry['query'] == real['query']
    assert dry['variables']['fields'] == real['variables']['fields']


def test_검증이_실패하면_쓰지_않는다():
    """내가 만든 문서가 틀렸으면 아무것도 건드리기 전에 안다."""
    page = Page([IDENTIFIED, Res({'errors': [{'message': 'Unknown field "nope"'}]})])
    with pytest.raises(gq.NotSent, match='Unknown field'):
        gq.save_fields(page, 'E1', {'nope': 'x'})
    assert writes(page) == []


def test_희소_업데이트다_보낸_필드만_간다():
    page = Page([IDENTIFIED, Res({'data': {}}), SAVED_CLEAN])
    gq.save_fields(page, 'E1', {'comment': '설명'})
    assert writes(page)[0]['data']['variables']['fields'] == {'comment': '설명'}


def test_저장_응답의_예외를_읽는다():
    """'유효한 정보를 제공해야 합니다' 류가 구조화돼서 온다. 화면 글자를 안 읽는다."""
    page = Page([IDENTIFIED, Res({'data': {}}), Res({'data': {'updateExpense': {
        'id': 'E1', 'entry': {'id': 'E1'},
        'entryExceptions': {'countOfExceptions': 1, 'hasBlockingExceptions': True,
                            'entryExceptions': [{
                                'exceptionCode': 'MISFLD', 'isBlocking': True,
                                'message': '날짜 범위를 입력해야 합니다',
                                'parameters': {'missingFields': {
                                    'fields': ['체크인 날짜'], 'fieldIds': ['x']}}}]}}}})])
    saved = gq.save_fields(page, 'E1', {'comment': 'x'})
    assert saved.blocking is True
    assert saved.messages == ('날짜 범위를 입력해야 합니다',)
    assert saved.missing == ('체크인 날짜',)


def test_보낸_뒤_실패는_재시도하지_않는다():
    """이중 저장을 막는다. 한 번만 보냈는지가 전부다."""
    page = Page([IDENTIFIED, Res({'data': {}}), Res({'errors': [{'message': 'boom'}]})])
    with pytest.raises(gq.MaybeSent):
        gq.save_fields(page, 'E1', {'comment': 'x'})
    assert len(writes(page)) == 1


def test_세션을_모르면_보내지_않는다():
    page = Page([Res({'data': {'employee': {'userId': None, 'contextRole': None,
                                            'expenseReport': None}}})])
    with pytest.raises(gq.NotSent):
        gq.save_fields(page, 'E1', {'comment': 'x'})
    assert page.context.request.sent and writes(page) == []


def test_경비_ID가_없으면_아무것도_보내지_않는다():
    page = Page([])
    with pytest.raises(gq.NotSent):
        gq.save_fields(page, '', {'comment': 'x'})
    assert page.context.request.sent == []


# --- 첨부 ------------------------------------------------------------------

ATTACHED = Res({'data': {'employee': {'expenseReport': {'entry': {
    'attachImage': {'id': 'E1', 'receiptImageId': 'IMG9'}}}}}})


def test_영수증은_올린_뒤_붙인다(tmp_path):
    slip = tmp_path / '2026-09-07-450000.pdf'
    slip.write_bytes(b'%PDF-1.4 x')
    page = Page([IDENTIFIED, Res({'imageId': 'IMG9', 'id': 'X'}),
                 Res({'data': {}}), ATTACHED])
    assert gq.attach_receipt(page, 'E1', slip) == 'IMG9'
    urls = [u for u, _ in page.context.request.sent]
    assert gq.UPLOAD_ENDPOINT in urls
    upload = next(kw for u, kw in page.context.request.sent if u == gq.UPLOAD_ENDPOINT)
    assert upload['multipart']['file']['name'] == slip.name
    assert upload['multipart']['file']['mimeType'] == 'application/pdf'


def test_파일이_없으면_아무것도_보내지_않는다(tmp_path):
    page = Page([])
    with pytest.raises(gq.NotSent, match='영수증 파일이 없습니다'):
        gq.attach_receipt(page, 'E1', tmp_path / '없음.pdf')
    assert page.context.request.sent == []


def test_업로드_실패는_되돌아가도_안전하다(tmp_path):
    """업로드는 경비를 건드리지 않는다. 떠 있는 이미지가 생길 뿐이다."""
    slip = tmp_path / 'a.jpg'
    slip.write_bytes(b'\xff\xd8')
    page = Page([IDENTIFIED, Res({}, 500)])
    with pytest.raises(gq.NotSent, match='HTTP 500'):
        gq.attach_receipt(page, 'E1', slip)


def test_붙이기가_보낸_뒤_실패하면_되돌아가지_않는다(tmp_path):
    slip = tmp_path / 'a.jpg'
    slip.write_bytes(b'\xff\xd8')
    page = Page([IDENTIFIED, Res({'imageId': 'IMG9'}), Res({'data': {}}),
                 Res({'errors': [{'message': 'boom'}]})])
    with pytest.raises(gq.MaybeSent):
        gq.attach_receipt(page, 'E1', slip)


# --- 드라이버가 어느 길로 가나 ----------------------------------------------

def plan_of(**kw):
    row = Row(index=0, when=None, amount=1000, text='', expense_id='E1')
    return Plan(row=row, type_code=kw.pop('type_code', None),
                type_label=kw.pop('type_label', '식음료'), **kw)


def test_목적_코멘트만이면_GraphQL로_간다(monkeypatch):
    calls = []
    monkeypatch.setattr(gq, 'save_fields',
                        lambda page, eid, fields: calls.append((eid, fields))
                        or gq.Saved(eid, False, (), ()))
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: pytest.fail('화면 경로로 갔습니다'))
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    driver.apply_edit(plan_of(purpose='내부 회의', comment='참석자 2명'))
    assert calls == [('E1', {'businessPurpose': '내부 회의', 'comment': '참석자 2명'})]


def test_유형_변경도_같은_mutation_으로_간다(monkeypatch):
    """fields.expenseTypeId 하나면 된다. policyId 는 우리 문서에 없다."""
    calls = []
    monkeypatch.setattr(gq, 'save_fields',
                        lambda page, eid, fields: calls.append(fields)
                        or gq.Saved(eid, False, (), ()))
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: pytest.fail('화면 경로로 갔습니다'))
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    driver.apply_edit(plan_of(type_code='LODNG', type_label='숙박비', comment='x'))
    assert calls == [{'expenseTypeId': 'LODNG', 'comment': 'x'}]


def test_유형_보고는_코드가_아니라_이름으로_한다(monkeypatch):
    monkeypatch.setattr(gq, 'save_fields',
                        lambda page, eid, fields: gq.Saved(eid, False, (), ()))
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    done = driver.apply_edit(plan_of(type_code='01143', type_label='사무용품'))
    assert '사무용품' in done and '01143' not in done


@pytest.mark.parametrize('extra', [
    {'lodging': object()},          # 숙박은 위치·채널이 이름이 아니라 ID라 남았다
])
def test_숙박은_화면_경로를_그대로_쓴다(monkeypatch, extra):
    monkeypatch.setattr(gq, 'save_fields',
                        lambda *a, **k: pytest.fail('GraphQL로 갔습니다'))
    went = []
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: went.append(1) or 'ok')
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    driver.apply_edit(plan_of(purpose='x', **extra))
    assert went == [1]


def test_보내기_전_실패면_화면으로_넘어간다(monkeypatch, capsys):
    monkeypatch.setattr(gq, 'save_fields',
                        lambda *a, **k: (_ for _ in ()).throw(gq.NotSent('세션 모름')))
    went = []
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: went.append(1) or 'ok')
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    driver.apply_edit(plan_of(comment='x'))
    assert went == [1]
    assert '화면으로 저장합니다' in capsys.readouterr().out


def test_보낸_뒤_실패면_화면으로_안_넘어간다(monkeypatch):
    """이중 저장을 막는다. 올려보내서 그 경비만 실패로 남긴다."""
    monkeypatch.setattr(gq, 'save_fields',
                        lambda *a, **k: (_ for _ in ()).throw(gq.MaybeSent('결과 불명')))
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: pytest.fail('화면 경로로 갔습니다'))
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    with pytest.raises(gq.MaybeSent):
        driver.apply_edit(plan_of(comment='x'))


def test_끄면_화면_경로로만_간다(monkeypatch):
    monkeypatch.setenv('AUTO_CONCUR_NO_GRAPHQL', '1')
    monkeypatch.setattr(gq, 'save_fields',
                        lambda *a, **k: pytest.fail('GraphQL로 갔습니다'))
    went = []
    monkeypatch.setattr('src.fix_expenses.apply_plan',
                        lambda *a, **k: went.append(1) or 'ok')
    driver = Driver(Page([]), 'https://eu2.concursolutions.com/nui/expense/reports/R1', None)
    driver.guard = lambda: None
    driver.apply_edit(plan_of(comment='x'))
    assert went == [1]
