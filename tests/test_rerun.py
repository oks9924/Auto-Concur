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


def test_화면_이름과_작업지_검색어를_맞춘다():
    # 실측: 화면 이름은 'Oh Kyungsik', 'Lee Kyungmin'. 작업지는 'kyungsik.oh'.
    from src.fix_expenses import name_matches

    화면 = ["Lee Kyungmin", "Oh Kyungsik"]
    작업지 = ["kyungsik.oh"]

    지울사람 = [n for n in 화면 if not any(name_matches(q, n) for q in 작업지)]
    넣을사람 = [q for q in 작업지 if not any(name_matches(q, n) for n in 화면)]
    assert 지울사람 == ["Lee Kyungmin"]
    assert 넣을사람 == []


def test_이름이_비슷해도_다른_사람은_안_맞는다():
    from src.fix_expenses import name_matches

    # kyungsik 과 kyungmin 은 다른 사람이다
    assert not name_matches("kyungsik.oh", "Lee Kyungmin")
    assert not name_matches("kyungmin.lee", "Oh Kyungsik")


def test_참석자가_비어_있으면_설정값을_쓴다(tmp_path):
    """작업지에 수식을 넣으면 이름을 덧붙이기 어렵다. 빈 칸으로 두고 여기서 채운다."""
    import csv

    from src import fix_expenses as fx
    from src import settings
    from src.attach_receipts import Row

    cols = ["거래일", "금액", "승인번호", "경비유형", "참석자", "비즈니스목적", "코멘트"]
    path = tmp_path / "m.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"거래일": "2026-07-02", "금액": "13500", "승인번호": "1",
                    "경비유형": "내부 직원간 식음료", "참석자": "",
                    "비즈니스목적": "", "코멘트": ""})

    cfg = {**settings.DEFAULTS, "attendee_default": "kyungsik.oh"}
    screen = [Row(0, date(2026, 7, 2), 13500, "", "ID1", "환급 불가", "CAFE")]
    plans, gaps, _ = fx.plans_from_sheet(cfg, screen, path, 1)
    assert plans[0][0].attendee == "kyungsik.oh"
    assert not gaps  # 설정으로 채워지므로 '빠진 값'이 아니다


def test_작업지에_적은_참석자가_우선이다(tmp_path):
    import csv

    from src import fix_expenses as fx
    from src import settings
    from src.attach_receipts import Row

    cols = ["거래일", "금액", "승인번호", "경비유형", "참석자", "비즈니스목적", "코멘트"]
    path = tmp_path / "m.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"거래일": "2026-07-02", "금액": "13500", "승인번호": "1",
                    "경비유형": "내부 직원간 식음료",
                    "참석자": "kyungsik.oh, hong.gildong",
                    "비즈니스목적": "", "코멘트": ""})

    cfg = {**settings.DEFAULTS, "attendee_default": "kyungsik.oh"}
    screen = [Row(0, date(2026, 7, 2), 13500, "", "ID1", "환급 불가", "CAFE")]
    plans, _, _ = fx.plans_from_sheet(cfg, screen, path, 1)
    assert fx.parse_attendees(plans[0][0].attendee) == ["kyungsik.oh", "hong.gildong"]


def test_식음료가_아니면_설정값을_넣지_않는다(tmp_path):
    import csv

    from src import fix_expenses as fx
    from src import settings
    from src.attach_receipts import Row

    cols = ["거래일", "금액", "승인번호", "경비유형", "참석자", "비즈니스목적", "코멘트"]
    path = tmp_path / "m.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"거래일": "2026-07-02", "금액": "13500", "승인번호": "1",
                    "경비유형": "주차비", "참석자": "",
                    "비즈니스목적": "", "코멘트": "출장 주차"})

    cfg = {**settings.DEFAULTS, "attendee_default": "kyungsik.oh"}
    screen = [Row(0, date(2026, 7, 2), 13500, "", "ID1", "환급 불가", "PARK")]
    plans, _, _ = fx.plans_from_sheet(cfg, screen, path, 1)
    assert plans[0][0].attendee == ""


def test_참석자는_수식_추가_참석자는_빈_칸(tmp_path):
    """참석자는 자동으로 채워지고, 사람이 손대는 칸은 '추가 참석자' 다."""
    from openpyxl import load_workbook

    from src import settings, sheet
    from src.organize import MANIFEST_COLUMNS

    cfg = {**settings.DEFAULTS, "attendee_default": "kyungsik.oh"}
    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    rows = [base | {"거래일": "2026-07-02", "금액": "13500", "승인번호": "1"}]
    path = tmp_path / "m.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, rows, path, settings.choices(cfg),
                     type_defaults=settings.type_defaults(cfg))

    ws = load_workbook(path)["전표"]
    참석자 = ws.cell(row=2, column=MANIFEST_COLUMNS.index("참석자") + 1)
    추가 = ws.cell(row=2, column=MANIFEST_COLUMNS.index("추가 참석자") + 1)
    assert 참석자.value == '=IF($M2="내부 직원간 식음료","kyungsik.oh","")'
    assert 추가.value in (None, "")


def test_추가_참석자_칸에_설명이_붙는다(tmp_path):
    """마우스를 올리면 뜨는 메모와, 칸을 고르면 뜨는 쪽지 둘 다 있어야 한다."""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    from src import settings, sheet
    from src.organize import MANIFEST_COLUMNS

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    rows = [base | {"거래일": "2026-07-02", "금액": "13500", "승인번호": "1"}]
    path = tmp_path / "m.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, rows, path, settings.choices(settings.DEFAULTS))

    ws = load_workbook(path)["전표"]
    col = get_column_letter(MANIFEST_COLUMNS.index("추가 참석자") + 1)
    assert "빈칸으로 두세요" in ws[f"{col}1"].comment.text

    쪽지 = [d for d in ws.data_validations.dataValidation
            if d.promptTitle == "추가 참석자"]
    assert 쪽지 and "빈칸으로 두세요" in 쪽지[0].prompt


def test_초록은_참석자와_추가_참석자_둘_다(tmp_path):
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    from src import settings, sheet
    from src.organize import MANIFEST_COLUMNS

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    rows = [base | {"거래일": "2026-07-02", "금액": "13500", "승인번호": "1"}]
    path = tmp_path / "m.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, rows, path, settings.choices(settings.DEFAULTS))

    ws = load_workbook(path)["전표"]
    칠한칸 = {str(r.sqref) for r in ws.conditional_formatting}
    for name in ("참석자", "추가 참석자"):
        assert f"{get_column_letter(MANIFEST_COLUMNS.index(name) + 1)}2" in 칠한칸
