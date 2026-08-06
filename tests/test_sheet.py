"""작업지 읽고 쓰기 테스트.

작업지가 Concur에 들어갈 값의 근거다. 잘못 읽으면 엉뚱한 값이 들어간다.
"""

from datetime import date

import pytest

from src import settings, sheet
from src.organize import MANIFEST_COLUMNS

ROWS = [
    {
        "파일명": "a.pdf", "거래일": "2026-07-01", "거래시각": "12:00:00",
        "합계": "75400", "승인번호": "00550817", "가맹점명": "쿠우쿠우울산동구점",
        "경비유형": "내부 직원간 식음료", "비즈니스목적": "현대 중공업 식대",
        "코멘트": "현대 중공업 출장 식대", "참석자": "kyungsik.oh",
    },
    {
        "파일명": "b.pdf", "거래일": "2026-07-19", "거래시각": "20:00:00",
        "합계": "180000", "승인번호": "00999999", "가맹점명": "라한호텔울산",
        "경비유형": "숙박비", "비즈니스목적": "", "코멘트": "", "참석자": "",
    },
]

TYPES = list(settings.DEFAULTS["expense_type_codes"])


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "manifest.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, ROWS, path, TYPES)
    return path


def test_엑셀로_쓰고_다시_읽는다(book):
    rows = sheet.load(book)
    assert [r.approval for r in rows] == ["00550817", "00999999"]
    assert rows[0].when == date(2026, 7, 1)
    assert rows[0].amount == 75400
    assert rows[0].attendee == "kyungsik.oh"
    assert rows[1].type_name == "숙박비" and rows[1].attendee == ""


def test_경비유형_칸에_드롭다운이_걸린다(book):
    # 오타로 없는 유형을 적으면 코드를 못 찾는다. 엑셀에서 막는다.
    from openpyxl import load_workbook

    wb = load_workbook(book)
    dv = wb["전표"].data_validations.dataValidation[0]
    assert dv.type == "list"
    assert f"$A${len(TYPES)}" in dv.formula1
    assert wb["경비유형"].sheet_state == "hidden"  # 목록 시트는 감춘다


def test_csv와_xlsx가_같은_결과를_준다(tmp_path, book):
    import csv

    path = tmp_path / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(ROWS)
    assert sheet.load(path) == sheet.load(book)


def test_승인번호가_빈_줄은_건너뛴다(tmp_path):
    import csv

    path = tmp_path / "m.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows([*ROWS, {"거래일": "2026-07-02", "합계": "1000", "승인번호": ""}])
    assert len(sheet.load(path)) == 2


def test_칼럼이_없으면_멈춘다(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("아무거나\n1\n", encoding="utf-8-sig")
    with pytest.raises(sheet.SheetError, match="칼럼이 없다"):
        sheet.load(path)


def test_숫자가_아니면_멈춘다(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("거래일,합계,승인번호\n2026-07-01,천원,001\n", encoding="utf-8-sig")
    with pytest.raises(sheet.SheetError, match="읽지 못했다"):
        sheet.load(path)
