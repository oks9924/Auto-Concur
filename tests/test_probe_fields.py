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


# --- 대조군 ---------------------------------------------------------------
# 대조군이 없으면 "전부 통과" 를 해석할 수 없다. 2026-11-05 에 그걸 빠뜨렸다.

def test_대조군도_전부_skip_으로_나간다():
    from src.probe_fields import controls
    for what, name, query, variables in controls('U1', 'TRAVELER'):
        assert '@skip(if: $skip)' in query, what


def test_대조군은_일부러_틀린_것들이다():
    from src.probe_fields import controls
    kinds = [what for what, *_ in controls('U1', 'TRAVELER')]
    assert len(kinds) == 4
    found = [(w, n, q, v) for w, n, q, v in controls('U1', 'TRAVELER')]
    assert any('atnKeyNOPE' in str(v) for _, _, _, v in found)       # 없는 입력 필드
    assert any('saveAttendeesLegacyNOPE' in q for _, _, q, _ in found)  # 없는 mutation
    assert any(v['params']['attendees'][0].get('versionNumber') == '숫자아님'
               for _, _, _, v in found)                              # 타입 불일치
    assert any('userId' not in v for _, _, _, v in found)            # 필수 변수 누락


def test_대조군이_통과하면_결과가_무의미하다고_말한다(monkeypatch, tmp_path, capsys):
    """서버가 @skip 붙은 호출을 아예 검사하지 않을 수 있다. 그러면 알려야 한다."""
    class Report:
        key = ('eu2.concursolutions.com', 'R1')
        title, url, rows = 'r', '', ()

    monkeypatch.setattr(gq, 'call', lambda *a, **k: {})      # 뭐든 통과시킨다
    monkeypatch.setattr(gq, 'identify', lambda ctx, rid: ('U1', 'TRAVELER'))
    monkeypatch.setattr('src.probe_fields.paths.at', lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr('src.probe_fields.open_report',
                        lambda automatic=False: (
                            type('P', (), {'stop': lambda s: None})(),
                            type('C', (), {'close': lambda s: None})(),
                            None, Report()))
    run()
    out = capsys.readouterr().out
    assert '아무 뜻이 없습니다' in out


def test_대조군이_거부되면_결과를_믿는다(monkeypatch, tmp_path, capsys):
    class Report:
        key = ('eu2.concursolutions.com', 'R1')
        title, url, rows = 'r', '', ()

    def picky(ctx, name, query, variables):
        """대조군만 거부하는 서버 흉내."""
        attendees = (variables.get('params') or {}).get('attendees') or [{}]
        if ('NOPE' in query or 'NOPE' in str(variables) or 'userId' not in variables
                or attendees[0].get('versionNumber') == '숫자아님'):
            raise gq.GraphQLError('거부')
        return {}

    monkeypatch.setattr(gq, 'call', picky)
    monkeypatch.setattr(gq, 'identify', lambda ctx, rid: ('U1', 'TRAVELER'))
    monkeypatch.setattr('src.probe_fields.paths.at', lambda *p: tmp_path.joinpath(*p))
    monkeypatch.setattr('src.probe_fields.open_report',
                        lambda automatic=False: (
                            type('P', (), {'stop': lambda s: None})(),
                            type('C', (), {'close': lambda s: None})(),
                            None, Report()))
    run()
    out = capsys.readouterr().out
    assert '위 결과는 뜻이 있습니다' in out
