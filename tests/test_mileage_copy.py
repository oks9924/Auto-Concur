"""마일리지 한 행을 여러 날짜로 복사한다. Tk 없이 도는 논리 검사."""
from datetime import date

from src.mileage import checked_row, copies_for_dates, new_row, taken_dates

WEEK = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]   # 11월 첫 주 월~금


def source(**over):
    row = {**new_row(), 'date': '2026-10-30', 'origin': '인천 계양구 (집)',
           'destination': '한국 알테어', 'vehicle': 'My Car', 'kind': 'long', 'rate': '280',
           'distance': '100', 'passengers': '0', 'description': '출근',
           'map': 'mileage-maps/a.png', 'map_sha256': 'x' * 64, 'map_name': 'a.png'}
    row.update(over)
    return checked_row(row)


def test_날짜만_다른_행이_날짜마다_하나씩_생긴다():
    src = source()
    made, skipped = copies_for_dates(src, WEEK, [src])
    assert [r['date'] for r in made] == ['2026-11-02', '2026-11-03', '2026-11-04',
                                         '2026-11-05', '2026-11-06']
    assert skipped == []
    for row in made:
        for key in ('origin', 'destination', 'vehicle', 'kind', 'rate', 'distance',
                    'passengers', 'description', 'map', 'map_sha256', 'map_name', 'estimate'):
            assert row[key] == src[key], key


def test_복사본은_각자_새_식별자를_갖는다():
    src = source()
    made, _ = copies_for_dates(src, WEEK, [src])
    ids = [r['id'] for r in made]
    assert len(set(ids)) == 5 and src['id'] not in ids


def test_Concur_상태는_복사하지_않는다():
    """딸려 가면 '이미 생성됨' 으로 보여 복사본이 영영 Concur에 안 만들어진다."""
    src = {**source(), 'concur_state': 'verified', 'concur_expense_id': 'EXP-OLD',
           'concur_receipt_verified': True}
    made, _ = copies_for_dates(src, WEEK, [src])
    assert made and not [k for row in made for k in row if k.startswith('concur_')]
    assert src['concur_expense_id'] == 'EXP-OLD'      # 원본은 그대로


def test_원본_날짜를_같이_골라도_두_벌이_되지_않는다():
    src = source(date='2026-11-02')
    made, skipped = copies_for_dates(src, WEEK, [src])
    assert [r['date'] for r in made] == ['2026-11-03', '2026-11-04', '2026-11-05', '2026-11-06']
    assert skipped == [date(2026, 11, 2)]


def test_이미_있는_같은_경로의_날짜는_건너뛴다():
    src = source()
    already = source(date='2026-11-04')
    made, skipped = copies_for_dates(src, WEEK, [src, already])
    assert date(2026, 11, 4) in skipped and len(made) == 4


def test_같은_날이라도_경로가_다르면_만든다():
    src = source()
    other = source(date='2026-11-04', destination='다른 곳')
    made, skipped = copies_for_dates(src, WEEK, [src, other])
    assert len(made) == 5 and skipped == []


def test_같은_날짜를_두_번_넘겨도_한_번만_날짜순으로():
    src = source()
    made, _ = copies_for_dates(src, [WEEK[2], WEEK[0], WEEK[2]], [src])
    assert [r['date'] for r in made] == ['2026-11-02', '2026-11-04']


def test_아무_날짜도_없으면_아무것도_안_만든다():
    src = source()
    assert copies_for_dates(src, [], [src]) == ([], [])


def test_달력에_표시할_이미_있는_날짜():
    src = source()
    rows = [src, source(date='2026-11-04'), source(date='2026-11-05', destination='다른 곳')]
    assert taken_dates(src, rows) == {date(2026, 10, 30), date(2026, 11, 4)}
