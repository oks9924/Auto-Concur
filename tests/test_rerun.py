"""이미 처리된 건이 섞여 있을 때의 규칙.

두 번째 실행이 첫 번째와 다르게 굴어야 한다. 영수증은 두 번 붙이면 안 되고,
유형·목적·코멘트는 작업지 값으로 다시 맞춰야 하고, 참석자는 이미 맞으면
건드리지 않아야 한다.
"""

from datetime import date

from src.attach_receipts import Row, Slip, match
from src.fix_expenses import name_matches


def row(index: int, amount: int, receipt=None) -> Row:
    return Row(index, date(2026, 7, 1), amount, "", f"ID{index}", "", "", receipt)


def slip(amount: int, approval: str) -> Slip:
    from pathlib import Path

    return Slip(Path("a.pdf"), date(2026, 7, 1), amount, "가맹점", approval)


def test_영수증이_있는_행을_구분한다():
    # 첨부 여부는 attach_phase가 이 값으로 가른다.
    rows = [row(0, 1000, True), row(1, 2000, False), row(2, 3000, None)]
    assert [r.has_receipt for r in rows] == [True, False, None]


def test_영수증_유무는_매칭에_영향을_주지_않는다():
    # 붙었는지는 매칭 다음에 가른다. 매칭에서 빼면 짝이 밀린다.
    pairs, skipped = match([slip(1000, "1")], [row(0, 1000, True)], 1)
    assert len(pairs) == 1 and not skipped


def test_참석자_이름_대조():
    # 작업지는 'kyungsik.oh', 화면은 'Oh Kyungsik' 이다.
    assert name_matches("kyungsik.oh", "Oh Kyungsik")
    assert name_matches("kyungsik.oh", "Oh Kyungsik (kyungsik.oh@x.com)")
    assert not name_matches("kyungsik.oh", "Kim Minsu")
    assert not name_matches("", "Oh Kyungsik")  # 빈 검색어는 아무나 맞추면 안 된다
