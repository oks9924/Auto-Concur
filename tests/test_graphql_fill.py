"""화면의 빈 자리만 채우고, 어긋나면 비운다.

대체가 아니라 교차검증이다. 경비가 한 달에 한 번이라 표본을 쌓아 판단할 수 없어서
지금 붙이는데, 그래서 더 보수적으로 붙인다 - 어긋나면 어느 쪽도 고르지 않는다.
"""
from datetime import date

import pytest

from src import concur_graphql as gq
from src.attach_receipts import Row


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    gq.forget()
    gq._TOLD.clear()
    monkeypatch.delenv('AUTO_CONCUR_NO_GRAPHQL', raising=False)


class FakePage:
    url = 'https://eu2.concursolutions.com/nui/expense/reports/R1/expenses'
    context = object()


def rows_of(*items):
    return [Row(index=i, when=w, amount=a, text='', expense_id=e, has_receipt=r)
            for i, (e, w, a, r) in enumerate(items)]


def api_of(*items):
    return [gq.Entry(expense_id=e, when=w, amount=a, has_receipt=r) for e, w, a, r in items]


def serve(monkeypatch, entries):
    monkeypatch.setattr(gq, '_cached', lambda ctx, report_id: entries)


def test_화면이_못_읽은_날짜를_채운다(monkeypatch, capsys):
    """9월에 '10/09/2026' 로 14건이 보류된 자리다."""
    serve(monkeypatch, api_of(('E1', date(2026, 9, 7), 450000, True)))
    rows = rows_of(('E1', None, 450000, True))
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].when == date(2026, 9, 7)
    assert '빈 값 1건을 채웠습니다' in capsys.readouterr().out


def test_어긋나면_어느_쪽도_고르지_않는다(monkeypatch, capsys):
    serve(monkeypatch, api_of(('E1', date(2026, 9, 7), 450000, True)))
    rows = rows_of(('E1', None, 999, True))      # 금액이 다르다
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].when == date(2026, 9, 7)      # 빈 자리는 채우고
    assert rows[0].amount is None                # 어긋난 자리는 비운다
    assert '어긋난 1건은 값을 비웠습니다' in capsys.readouterr().out


def test_화면이_읽은_값을_덮어쓰지_않는다(monkeypatch):
    serve(monkeypatch, api_of(('E1', date(2026, 9, 7), 450000, True)))
    rows = rows_of(('E1', date(2026, 9, 7), 450000, True))
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].when == date(2026, 9, 7) and rows[0].amount == 450000


def test_영수증_유무도_빈_자리만_채운다(monkeypatch):
    serve(monkeypatch, api_of(('E1', date(2026, 9, 7), 1, True)))
    rows = rows_of(('E1', date(2026, 9, 7), 1, None))
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].has_receipt is True


def test_영수증_판정이_어긋나면_비운다(monkeypatch):
    serve(monkeypatch, api_of(('E1', date(2026, 9, 7), 1, False)))
    rows = rows_of(('E1', date(2026, 9, 7), 1, True))
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].has_receipt is None


def test_GraphQL이_모르는_값은_건드리지_않는다(monkeypatch):
    serve(monkeypatch, api_of(('E1', None, None, None)))
    rows = rows_of(('E1', date(2026, 9, 7), 450000, True))
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].when == date(2026, 9, 7) and rows[0].amount == 450000


def test_짝이_없는_경비는_건드리지_않는다(monkeypatch):
    serve(monkeypatch, api_of(('OTHER', date(2026, 1, 1), 1, True)))
    rows = rows_of(('E1', None, None, None))
    gq.fill_gaps(FakePage(), rows)
    assert rows[0].when is None and rows[0].amount is None


def test_값이_다_차_있어도_교차검증은_한다(monkeypatch):
    """빈 자리가 있을 때만 물으면 '어긋남' 을 놓친다. 비용은 캐시가 막는다."""
    called = []
    monkeypatch.setattr(gq, '_cached', lambda ctx, rid: called.append(rid) or [])
    gq.fill_gaps(FakePage(), rows_of(('E1', date(2026, 9, 7), 1, True)))
    assert called == ['R1']


def test_GraphQL이_실패해도_화면_값으로_간다(monkeypatch, capsys):
    """보완이 본 작업을 막아서는 안 된다."""
    def boom(ctx, report_id):
        raise RuntimeError('context closed')
    monkeypatch.setattr(gq, '_cached', boom)
    rows = rows_of(('E1', None, 450000, True))
    assert gq.fill_gaps(FakePage(), rows) is rows
    assert rows[0].when is None                 # 지금 동작 그대로
    assert '건너뜁니다' in capsys.readouterr().out


def test_호출_실패는_한_번만_알린다(monkeypatch, capsys):
    ctx = type('C', (), {'request': type('R', (), {
        'post': staticmethod(lambda *a, **k: (_ for _ in ()).throw(RuntimeError('down')))})()})()
    page = FakePage()
    page.context = ctx
    for _ in range(3):
        gq.forget()
        gq.fill_gaps(page, rows_of(('E1', None, 1, True)))
    assert capsys.readouterr().out.count('쓰지 못했습니다') == 1


def test_환경변수로_끌_수_있다(monkeypatch):
    monkeypatch.setenv('AUTO_CONCUR_NO_GRAPHQL', '1')
    called = []
    monkeypatch.setattr(gq, '_cached', lambda ctx, rid: called.append(rid) or [])
    rows = rows_of(('E1', None, None, None))
    gq.fill_gaps(FakePage(), rows)
    assert called == [] and rows[0].when is None


def test_리포트_주소가_아니면_넘어간다(monkeypatch):
    called = []
    monkeypatch.setattr(gq, '_cached', lambda ctx, rid: called.append(rid) or [])
    page = FakePage()
    page.url = 'https://eu2.concursolutions.com/home'
    gq.fill_gaps(page, rows_of(('E1', None, None, None)))
    assert called == []


def test_캐시는_같은_리포트를_다시_안_묻는다(monkeypatch):
    hits = []

    def once(ctx, report_id):
        hits.append(report_id)
        return api_of(('E1', date(2026, 9, 7), 1, True))
    monkeypatch.setattr(gq, 'identify', lambda ctx, rid: ('U1', 'TRAVELER'))
    monkeypatch.setattr(gq, 'entries', lambda ctx, rid, u, r: once(ctx, rid))
    for _ in range(3):
        gq.fill_gaps(FakePage(), rows_of(('E1', None, 1, True)))
    assert hits == ['R1']
    gq.forget('R1')
    gq.fill_gaps(FakePage(), rows_of(('E1', None, 1, True)))
    assert hits == ['R1', 'R1']


def test_어떤_예외도_본_작업을_막지_않는다(capsys):
    """page 가 우리가 생각한 모양이 아니어도 지금까지 되던 것은 그대로 돼야 한다."""
    rows = rows_of(('E1', None, 450000, True))
    assert gq.fill_gaps(object(), rows) is rows      # url 도 context 도 없는 객체
    assert rows[0].when is None
    assert '건너뜁니다' in capsys.readouterr().out
