"""숙박비 계산 회귀 테스트.

일일 객실 요금의 합이 경비 금액과 1원이라도 다르면 Concur가 저장을 막거나,
저장돼도 나중에 정산에서 걸린다. 그 규칙을 여기서 고정한다.
"""

from datetime import date

import pytest

from src import sheet
from src.fix_expenses import Lodging
from src.sheet import nightly_split


@pytest.mark.parametrize(
    "amount,nights",
    [(1_000_000, 3), (999_999, 6), (75_400, 1), (100, 7), (350_000, 2), (1, 1)],
)
def test_합은_언제나_금액과_같다(amount, nights):
    per = nightly_split(amount, nights)
    assert len(per) == nights
    assert sum(per) == amount


def test_나머지는_앞_날짜부터_1원씩():
    # 1,000,000 / 3 = 333,333.33. 버리면 1원이 빈다.
    assert nightly_split(1_000_000, 3) == [333_334, 333_333, 333_333]


def test_소수점은_들어가지_않는다():
    assert all(isinstance(x, int) for x in nightly_split(1_000_000, 7))


def test_숙박일수가_0이면_멈춘다():
    with pytest.raises(sheet.SheetError, match="숙박일수"):
        nightly_split(100_000, 0)


def test_입실_퇴실로_숙박일수를_센다():
    # 8/2 입실, 8/8 퇴실이면 6박이다.
    stay = Lodging(date(2026, 8, 2), date(2026, 8, 8), "울산", "직접 예약")
    assert stay.nights == 6
    assert stay.dates() == [date(2026, 8, d) for d in range(2, 8)]


def test_명세_날짜와_금액_개수가_맞는다():
    stay = Lodging(date(2026, 8, 2), date(2026, 8, 8), "", "")
    assert len(stay.dates()) == len(nightly_split(1_000_000, stay.nights))


def test_퇴실이_입실보다_앞이면_작업지에서_멈춘다(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "거래일,금액,승인번호,입실날짜,퇴실날짜\n2026-08-02,900000,1,2026-08-08,2026-08-02\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(sheet.SheetError, match="퇴실날짜"):
        sheet.load(path)


def test_한쪽만_적으면_멈춘다(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "거래일,금액,승인번호,입실날짜,퇴실날짜\n2026-08-02,900000,1,2026-08-02,\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(sheet.SheetError, match="둘 다"):
        sheet.load(path)


def test_엑셀에서_8슬래시2로_적어도_읽는다(tmp_path):
    from src.organize import MANIFEST_COLUMNS

    rows = [
        {
            "거래일": "2026-08-02", "금액": "900000", "승인번호": "1",
            "경비유형": "숙박비", "입실날짜": "2026-08-02", "퇴실날짜": "2026-08-08",
            "숙박위치": "울산", "Booking Channel": "직접 예약",
        }
    ]
    path = tmp_path / "m.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, rows, path, {"경비유형": ["숙박비"]})

    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    ws = load_workbook(path)["전표"]
    cell = ws.cell(row=2, column=MANIFEST_COLUMNS.index("입실날짜") + 1)
    assert cell.value.date() == date(2026, 8, 2)
    assert cell.number_format == sheet.DATE_FORMAT  # 화면에는 8/2 로 보인다
    assert get_column_letter(MANIFEST_COLUMNS.index("퇴실날짜") + 1)

    loaded = sheet.load(path)[0]
    assert loaded.nights == 6
    assert loaded.location == "울산" and loaded.channel == "직접 예약"


def test_숙박비인데_날짜가_없으면_짚어준다():
    # 코멘트만 넣고 지나가면 다 된 것처럼 보인다. 빠진 값을 알려줘야 한다.
    from src.fix_expenses import _gaps

    class Entry:
        checkin = checkout = None
        location = channel = attendee = ""

    holes = _gaps(Entry(), "숙박비")
    assert "입실·퇴실 날짜" in holes and "숙박 위치" in holes


def test_식음료인데_참석자가_없으면_짚어준다():
    from src.fix_expenses import _gaps

    class Entry:
        checkin = checkout = None
        location = channel = attendee = ""

    assert _gaps(Entry(), "내부 직원간 식음료") == ["참석자"]
