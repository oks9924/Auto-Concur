"""쓰지 않고 필수 필드를 시험한다. 안전 성질이 본문이다.

이 도구는 mutation 문서를 보낸다. 그러므로 **모든 시험이 skip=true 로 나가는 것**이
전부다. 하나라도 skip 없이 나가면 실제 저장이 일어난다.
"""
import pytest

from src import concur_graphql as gq
from src.probe_fields import (ROOM_RATE_WITH, ROOM_RATE_WITHOUT, attendee,
                              probes, reason, run)


def test_모든_시험_문서에_skip_이_있다():
    for what, name, query, variables in probes('U1', 'TRAVELER'):
        assert '@skip(if: $skip)' in query, what
        assert '$skip: Boolean!' in query, what


def test_필드를_하나씩만_뺀다():
    full = attendee()
    assert set(full) == {'atnKey', 'atnTypeCode', 'versionNumber',
                         'instanceCount', 'transactionAmount'}
    assert 'transactionAmount' not in attendee('transactionAmount')
    assert set(attendee('transactionAmount')) == set(full) - {'transactionAmount'}


def test_명세는_인자를_뺀_문서를_따로_쓴다():
    """변수가 아니라 필드 인자라서 변수만 빼면 시험이 안 된다."""
    assert 'roomRateExpenseTypeId: $roomRateExpenseTypeId' in ROOM_RATE_WITH
    assert 'roomRateExpenseTypeId' not in ROOM_RATE_WITHOUT
    assert 'createSameRoomRateItemizations' in ROOM_RATE_WITHOUT


def test_실제로_보낼_때도_전부_skip_참이다(monkeypatch, tmp_path):
    """이 테스트가 이 도구의 안전 성질 그 자체다."""
    sent = []

    def fake_call(ctx, name, query, variables):
        sent.append((name, query, variables))
        return {}

    class Report:
        key = ('eu2.concursolutions.com', 'R1')
        title, url, rows = 'r', '', ()

    monkeypatch.setattr(gq, 'call', fake_call)
    monkeypatch.setattr(gq, 'identify', lambda ctx, rid: ('U1', 'TRAVELER'))
    monkeypatch.setattr('src.probe_fields.paths.at', lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr('src.probe_fields.open_report',
                        lambda automatic=False: (
                            type('P', (), {'stop': lambda s: None})(),
                            type('C', (), {'close': lambda s: None})(),
                            None, Report()))
    assert run() == 0
    assert sent, '시험이 하나도 안 나갔습니다'
    for name, query, variables in sent:
        assert variables.get('skip') is True, f'{name} 이 skip 없이 나갔습니다'


def test_거부_사유에서_본문만_뽑는다():
    assert reason(gq.GraphQLError('X 호출이 HTTP 400 를 받았습니다 — {"errors":[...]}')) \
        == '{"errors":[...]}'
    assert reason(gq.GraphQLError('그냥 사유')) == '그냥 사유'
