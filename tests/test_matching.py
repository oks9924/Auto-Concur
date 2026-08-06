"""매칭 규칙 회귀 테스트.

이 프로젝트에서 틀리면 제일 비싼 부분이다. 전표를 엉뚱한 경비에 붙이면
나중에 감사에서 문제가 되고, 조용히 틀리기까지 한다. 그래서 '모호하면
건너뛴다'는 규칙을 테스트로 고정해둔다.
"""

from datetime import date
from pathlib import Path

from src.attach_receipts import Row, Slip, _parse_amount, expense_url, match

REPORT = "https://eu2.concursolutions.com/nui/expense/reports/E74C62516B624F0D91DD"


def slip(day: int, amount: int, month: int = 7, merchant: str = "가맹점") -> Slip:
    return Slip(Path(f"{amount}.pdf"), date(2026, month, day), amount, merchant, "0001")


def row(index: int, day: int, amount: int, month: int = 7, vendor: str = "") -> Row:
    return Row(index, date(2026, month, day), amount, f"{day} | {amount}", vendor=vendor)


def test_날짜와_금액이_같으면_붙인다():
    pairs, skipped = match([slip(1, 75400)], [row(0, 1, 75400)], 1)
    assert len(pairs) == 1 and not skipped


def test_자정_근처_거래는_하루_차이를_허용한다():
    # 06/30 20:59 결제가 Concur에는 07/01로 들어오는 경우.
    pairs, _ = match([slip(30, 22900, month=6)], [row(0, 1, 22900)], 1)
    assert len(pairs) == 1


def test_이틀_차이는_안_붙인다():
    pairs, skipped = match([slip(1, 22900)], [row(0, 3, 22900)], 1)
    assert not pairs and skipped[0][1] == "후보 없음"


def test_금액이_다르면_안_붙인다():
    pairs, skipped = match([slip(1, 22900)], [row(0, 1, 22901)], 1)
    assert not pairs and skipped


def test_같은_날_같은_금액이_둘이면_순서대로_배정한다():
    # 커피 두 잔. 날짜와 금액이 같으면 어느 쪽이든 된다고 보기로 했다.
    pairs, skipped = match([slip(2, 14000)], [row(0, 2, 14000), row(1, 2, 14000)], 1)
    assert len(pairs) == 1 and not skipped
    assert pairs[0][1].index == 0 and pairs[0][2] == "순서"


def test_후보가_여럿이면_가맹점으로_가른다():
    # manifest는 한글, Concur는 로마자다. 옮겨서 견준다.
    rows = [row(0, 16, 450000, vendor="NAVER FINANCIAL TRIP COM"),
            row(1, 16, 450000, vendor="RA HAN HO TEL UL SAN")]
    pairs, _ = match([slip(16, 450000, merchant="라한호텔울산")], rows, 1)
    assert pairs[0][1].index == 1 and pairs[0][2] == "가맹점"


def test_가맹점이_비슷하지_않으면_순서로_간다():
    rows = [row(0, 16, 450000, vendor="AAA BBB"), row(1, 16, 450000, vendor="CCC DDD")]
    pairs, _ = match([slip(16, 450000, merchant="라한호텔울산")], rows, 1)
    assert pairs[0][2] == "순서"


def test_한_행은_한_번만_쓴다():
    # 전표 둘이 같은 행을 두고 다투면 첫 전표만 가져가고 나머지는 건너뛴다.
    pairs, skipped = match([slip(1, 5000), slip(1, 5000)], [row(0, 1, 5000)], 1)
    assert len(pairs) == 1 and len(skipped) == 1


def test_금액_파싱():
    assert _parse_amount("19,800") == 19800
    assert _parse_amount("19,800 원") == 19800
    assert _parse_amount("KRW 75,400") == 75400
    assert _parse_amount("2026-07-31") is None
    assert _parse_amount("영수증 없음") is None


def test_상세_주소_만들기():
    # 행 id가 곧 경비 ID다. 클릭하지 않고 주소로 바로 간다.
    assert expense_url(REPORT, "ABC123") == REPORT + "/expenses/ABC123"


def test_이미_상세에_있어도_주소가_겹치지_않는다():
    assert expense_url(REPORT + "/expenses/OLD", "NEW") == REPORT + "/expenses/NEW"
    assert expense_url(REPORT + "/?x=1", "NEW") == REPORT + "/expenses/NEW"
