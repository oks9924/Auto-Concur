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


def test_빈_항목별_명세는_추가_버튼을_누른다():
    """'항목별 명세 없음' 화면에는 '반복' 콤보박스가 없다. 버튼부터 눌러야 생긴다."""
    from src.fix_expenses import itemization_step

    화면 = {"form": False, "add": True, "empty": True}
    assert itemization_step(화면) == "add"


def test_이미_있는_명세는_추가를_누르지_않는다():
    """명세가 있을 때도 '추가' 버튼은 있다. 누르면 필요 없는 명세가 하나 더 생긴다."""
    from src.fix_expenses import itemization_step

    화면 = {"form": False, "add": True, "empty": False}
    assert itemization_step(화면) == "skip"


def test_입력_폼이_떠_있으면_바로_채운다():
    from src.fix_expenses import itemization_step

    assert itemization_step({"form": True, "add": True, "empty": False}) == "fill"


def test_1박은_반복_칸을_찾지_않는다():
    """반복할 밤이 없어서 '일일 금액 동일/다름' 칸이 아예 안 나온다."""
    from src.fix_expenses import needs_recurrence

    assert not needs_recurrence(1, lambda: False)
    assert nightly_split(120_000, 1) == [120_000]  # 한 행이면 동일이든 다름이든 같다


def test_1박이라도_칸이_있으면_고른다():
    from src.fix_expenses import needs_recurrence

    assert needs_recurrence(1, lambda: True)


def test_여러_박이면_화면을_보지_않고_고른다():
    """아직 안 그려졌을 때 미리 보고 '없다'고 판단하면 안 된다."""
    from src.fix_expenses import needs_recurrence

    def 보면안됨():
        raise AssertionError("여러 박이면 화면을 볼 것도 없이 골라야 한다")

    assert needs_recurrence(6, 보면안됨)


def test_화면_상태는_보이는_글자로_읽는다():
    """셀렉터를 추측하지 않는다. 화면에 뜨는 글자가 근거다."""
    from src.fix_expenses import (
        ADD_ITEMIZATION_JS,
        ADD_ITEMIZATION_TEXT,
        EMPTY_ITEMIZATION_TEXT,
        ITEMIZATION_STATE_JS,
    )

    assert EMPTY_ITEMIZATION_TEXT in ITEMIZATION_STATE_JS
    assert ADD_ITEMIZATION_TEXT in ITEMIZATION_STATE_JS
    assert ADD_ITEMIZATION_TEXT in ADD_ITEMIZATION_JS


def test_숨은_저장_버튼은_뒤로_밀되_버리지_않는다():
    """실측 두 번.

    1) 화면 밖의 exp-save-expense-hidden 이 먼저 걸려 30초를 기다리다 실패했다.
    2) 그래서 목록에서 아예 뺐더니 '저장 버튼을 찾지 못했습니다'로 그냥
       넘어가버렸다. 빼지 말고 순서만 뒤로 미는 것이 맞다.
    """
    from src.fix_expenses import SAVE_BUTTONS_JS

    assert "save-hidden-button" in SAVE_BUTTONS_JS
    assert "found.sort" in SAVE_BUTTONS_JS  # 거르는 게 아니라 순서만 민다
    assert "return found.map" in SAVE_BUTTONS_JS  # 후보를 전부 돌려준다


def _숙박흐름(monkeypatch, *, 날짜바뀜, 위치바뀜, 명세):
    """_apply_lodging 을 화면 없이 돌린다. 어디를 눌렀는지만 본다."""
    from src import fix_expenses as fx
    from src.attach_receipts import Row

    한일 = []
    monkeypatch.setattr(fx, "_open_tab", lambda p, sel, what: 한일.append(f"탭:{what}"))
    monkeypatch.setattr(fx, "_set_date_range", lambda p, a, b: 날짜바뀜)
    monkeypatch.setattr(fx, "_pick_from_combo", lambda p, h, w, what: 위치바뀜)
    monkeypatch.setattr(fx, "_itemization_ready", lambda p: 명세)
    monkeypatch.setattr(fx, "_fill_room_rates", lambda p, a: "요금")
    monkeypatch.setattr(fx, "needs_recurrence", lambda n, f: False)
    monkeypatch.setattr(fx, "_save_expense",
                        lambda p, r, u, labels=fx.SAVE_DETAIL, reopen=True: 한일.append(f"저장:{labels}"))

    plan = fx.Plan(
        row=Row(0, date(2026, 7, 5), 711_620, "숙박비", "ID1", "숙박비", "HOTEL"),
        type_code=None, type_label="숙박비", purpose="", comment="", attendee="",
        lodging=Lodging(date(2026, 7, 5), date(2026, 7, 6), "국내", "Others"),
    )
    fx._apply_lodging(None, plan, "http://x", changed=False)
    return 한일


def test_바뀐_것이_없으면_저장하지_않는다(monkeypatch):
    """실측 2026-07-05 711,620원: 다 되어 있는 건에서 저장 버튼만 찾다 실패했다."""
    한일 = _숙박흐름(monkeypatch, 날짜바뀜=False, 위치바뀜=False, 명세="이미 있음")
    assert not [x for x in 한일 if x.startswith("저장")]


def test_명세가_있어도_상세를_바꿨으면_저장한다(monkeypatch):
    """탭을 눌러 상세로 돌아가지 않는다.

    명세 화면이 전체 화면 사이드 패널로 열리면 그 패널이 탭을 덮어서 클릭이
    30초 동안 막힌다 (실측 2026-09-09). 지금 화면에서 누를 수 있는 저장
    버튼을 쓴다 - 여기서는 명세를 건드리지 않았으므로 어느 쪽이든 같다.
    """
    from src.fix_expenses import SAVE_ANYWHERE

    한일 = _숙박흐름(monkeypatch, 날짜바뀜=True, 위치바뀜=False, 명세="이미 있음")
    assert 한일[-1] == f"저장:{SAVE_ANYWHERE}"
    assert "탭:상세 정보" not in 한일[-2:]  # 덮인 탭을 누르러 가지 않는다


def test_명세를_채웠으면_항목별_명세_저장으로_저장한다(monkeypatch):
    from src.fix_expenses import SAVE_ITEMIZATION

    한일 = _숙박흐름(monkeypatch, 날짜바뀜=False, 위치바뀜=False, 명세=None)
    assert 한일[-1] == f"저장:{SAVE_ITEMIZATION}"
    assert "항목별 명세 저장" in SAVE_ITEMIZATION


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


def test_숙박비를_고른_행에만_기본값이_나온다(tmp_path):
    """유형을 고르기 전에는 무엇이 맞는지 모른다. 그래서 값이 아니라 수식을 넣는다."""
    from openpyxl import load_workbook

    from src import settings
    from src.organize import MANIFEST_COLUMNS
    from src.settings import type_defaults

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    rows = [base | {"거래일": "2026-08-02", "금액": "450000", "승인번호": "1"}]
    path = tmp_path / "m.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, rows, path, settings.choices(settings.DEFAULTS),
                     type_defaults=settings.type_defaults(settings.DEFAULTS))

    ws = load_workbook(path)["전표"]
    셀 = ws.cell(row=2, column=MANIFEST_COLUMNS.index("숙박위치") + 1)
    assert 셀.value == '=IF($M2="숙박비","국내","")'
    셀 = ws.cell(row=2, column=MANIFEST_COLUMNS.index("Booking Channel") + 1)
    assert 셀.value == '=IF($M2="숙박비","Others","")'

    # 기본값은 드롭다운 목록 안에 있어야 한다. 아니면 사람이 고칠 수도 없다.
    고를수있는값 = settings.choices(settings.DEFAULTS)
    기본값 = type_defaults(settings.DEFAULTS)["숙박비"]
    assert 기본값["숙박위치"] in 고를수있는값["숙박위치"]
    assert 기본값["Booking Channel"] in 고를수있는값["Booking Channel"]


def test_사람이_적은_값은_수식으로_덮이지_않는다(tmp_path):
    from openpyxl import load_workbook

    from src import settings
    from src.organize import MANIFEST_COLUMNS

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    rows = [base | {"거래일": "2026-08-02", "금액": "450000", "승인번호": "1", "숙박위치": "해외"}]
    path = tmp_path / "m.xlsx"
    sheet.write_xlsx(MANIFEST_COLUMNS, rows, path, settings.choices(settings.DEFAULTS),
                     type_defaults=settings.type_defaults(settings.DEFAULTS))

    ws = load_workbook(path)["전표"]
    assert ws.cell(row=2, column=MANIFEST_COLUMNS.index("숙박위치") + 1).value == "해외"


def test_칸이_비어_있으면_설정_기본값도_쓰지_않는다(tmp_path):
    """빈 칸은 Concur의 현재 값을 유지하라는 지시다."""
    import csv

    from src import fix_expenses as fx
    from src import settings
    from src.attach_receipts import Row

    cols = ["거래일", "금액", "승인번호", "경비유형", "입실날짜", "퇴실날짜", "숙박위치",
            "Booking Channel", "비즈니스목적", "코멘트", "참석자"]
    path = tmp_path / "m.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerow({"거래일": "2026-08-02", "금액": "450000", "승인번호": "1",
                    "경비유형": "숙박비", "입실날짜": "2026-08-02", "퇴실날짜": "2026-08-08",
                    "숙박위치": "", "Booking Channel": "", "비즈니스목적": "",
                    "코멘트": "", "참석자": ""})

    screen = [Row(0, date(2026, 8, 2), 450000, "숙박비", "ID1", "숙박비", "HOTEL")]
    plans, _, _ = fx.plans_from_sheet(settings.DEFAULTS, screen, path, 1)
    stay = plans[0][0].lodging
    assert stay.location == "" and stay.channel == ""


def test_Concur가_거부하면_성공으로_세지_않는다():
    """실측 2026-09-09: 숙박비 3건이 날짜 범위 없이 저장됐는데 로그는 성공이었다.

      (저장 후 안내창을 닫았습니다: 오류 계속하려면 다음에 관한 유효한 정보를
       제공해야 합니다. 날짜 범위 닫기)
      [1/4] 2026-08-12 396,000원 - 코멘트

    화면에는 빨간 오류가 남았는데 '처리했습니다' 로 찍혔다. 저장이 안 된 것을
    성공으로 세면 사람이 그 건을 다시 안 본다.
    """
    from src.fix_expenses import REJECTED_RE

    거부 = "오류 계속하려면 다음에 관한 유효한 정보를 제공해야 합니다. 날짜 범위 닫기"
    assert REJECTED_RE.search(거부)
    # 그냥 안내는 막지 않는다 - 그건 저장이 된 것이다
    assert not REJECTED_RE.search("이 경비가 저장되었습니다")
    assert not REJECTED_RE.search("참석자 목록을 저장하시겠습니까?")


def test_빠진_날짜는_읽은_값까지_보여준다():
    """'적었는데 왜 안 넣냐'가 되면, 잘못 읽은 건지 정말 빈 건지 갈려야 한다."""
    import inspect

    from src import fix_expenses as fx

    소스 = inspect.getsource(fx.fix_phase)
    assert "작업지에서 읽은 값" in 소스
    assert "entry.checkin!r" in 소스


def test_엑셀에_적은_값이_B단계를_다시_눌러도_남는다(tmp_path):
    """실측 2026-09-09: 숙박 날짜를 엑셀에 적어뒀는데 C단계가 날짜 칸을 아예
    건드리지 않았다.

    사람이 여는 것은 manifest.xlsx 인데, 다시 만들 때 참고하는 것은
    manifest.csv 뿐이었다. 엑셀에 적은 값은 아무도 읽지 않았고 B단계를 다시
    누를 때마다 통째로 사라졌다.
    """
    import csv
    import os

    from src import settings
    from src.organize import MANIFEST_COLUMNS, _kept_edits

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    적은것 = base | {
        "거래일": "2026-08-12", "금액": "396000", "승인번호": "A1",
        "경비유형": "숙박비", "코멘트": "현대 중공업 엔진 출장 숙박",
        "입실날짜": "2026-08-17", "퇴실날짜": "2026-08-21",
        "숙박위치": "국내", "Booking Channel": "Others",
    }
    sheet.write_xlsx(MANIFEST_COLUMNS, [적은것], tmp_path / "manifest.xlsx",
                     settings.choices(settings.DEFAULTS))
    # csv 에는 그 값이 없다. B단계가 만든 그대로다.
    with (tmp_path / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerow(base | {"거래일": "2026-08-12", "금액": "396000", "승인번호": "A1"})
    os.utime(tmp_path / "manifest.xlsx", None)  # 엑셀을 나중에 고쳤다

    기억 = _kept_edits(tmp_path)["A1"]
    assert 기억["입실날짜"].startswith("2026-08-17")
    assert 기억["퇴실날짜"].startswith("2026-08-21")
    assert 기억["숙박위치"] == "국내" and 기억["코멘트"] == "현대 중공업 엔진 출장 숙박"


def test_csv를_나중에_고쳤으면_그쪽을_믿는다(tmp_path):
    """두 파일은 B단계가 같이 만든다. 그 뒤로 사람이 손댄 쪽이 더 최근이다."""
    import csv
    import os
    import time

    from src import settings
    from src.organize import MANIFEST_COLUMNS, _kept_edits

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    sheet.write_xlsx(MANIFEST_COLUMNS, [base | {"거래일": "2026-08-12", "금액": "1",
                                                "승인번호": "A1", "코멘트": "엑셀"}],
                     tmp_path / "manifest.xlsx", settings.choices(settings.DEFAULTS))
    time.sleep(0.01)
    with (tmp_path / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerow(base | {"거래일": "2026-08-12", "금액": "1",
                           "승인번호": "A1", "코멘트": "csv"})
    os.utime(tmp_path / "manifest.csv", None)

    assert _kept_edits(tmp_path)["A1"]["코멘트"] == "csv"


def test_수식은_수식으로_남는다(tmp_path):
    """참석자·숙박위치는 유형을 고르면 따라오는 수식이다.

    계산된 값을 가져오면 수식이 값으로 굳어서, 나중에 유형을 바꿔도 안 따라간다.
    """
    from src import settings
    from src.organize import MANIFEST_COLUMNS, _edits_from_xlsx

    base = dict.fromkeys(MANIFEST_COLUMNS, "")
    sheet.write_xlsx(MANIFEST_COLUMNS, [base | {"거래일": "2026-08-12", "금액": "1",
                                                "승인번호": "A1"}],
                     tmp_path / "manifest.xlsx", settings.choices(settings.DEFAULTS),
                     type_defaults=settings.type_defaults(settings.DEFAULTS))

    assert _edits_from_xlsx(tmp_path / "manifest.xlsx")["A1"]["숙박위치"].startswith("=IF(")


def test_날짜_없는_숙박비는_계획_줄에서_보인다():
    """실측 2026-09-09: '숙박비 -> 코멘트' 만 찍히고 날짜 칸은 손도 안 댔다.

    저장해봐야 Concur가 거부한다. 실패한 뒤에 이유를 찾느니 계획에서 보이게 한다.
    """
    import inspect

    from src import fix_expenses as fx

    소스 = inspect.getsource(fx.fix_phase)
    assert "입실·퇴실 날짜가 작업지에 없습니다" in 소스
    assert "not plan.lodging" in 소스


def test_옛_작업지에_숙박_칸이_없으면_알린다(tmp_path, capsys):
    """숙박비 칸은 나중에 생겼다. 옛 작업지에는 아예 없다.

    그러면 사람이 어딘가에 날짜를 적어놔도 우리는 못 읽고, '적었는데 왜 안
    넣냐'가 된다. 칸이 없다는 것부터 말해야 한다.
    """
    path = tmp_path / "old.csv"
    path.write_text(
        "거래일,금액,승인번호,경비유형,코멘트\n2026-08-12,396000,A1,숙박비,출장 숙박\n",
        encoding="utf-8-sig",
    )
    rows = sheet.load(path)
    assert rows[0].comment == "출장 숙박"  # 있는 칸은 그대로 읽는다
    assert rows[0].checkin is None

    말 = capsys.readouterr().out
    assert "입실날짜" in 말 and "퇴실날짜" in 말
    assert "B단계로 작업지를 새로 만들어" in 말


def test_빈_칸은_건드리지_않는다(tmp_path):
    """거래일·금액만 있고 사람이 아무것도 안 적은 줄은 대상이 아니다."""
    import csv

    from src import fix_expenses as fx
    from src import settings
    from src.attach_receipts import Row

    cols = ["거래일", "금액", "승인번호", "경비유형", "비즈니스목적", "코멘트"]
    path = tmp_path / "m.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerow({"거래일": "2026-08-12", "금액": "396000", "승인번호": "A1",
                    "경비유형": "", "비즈니스목적": "", "코멘트": ""})

    screen = [Row(0, date(2026, 8, 12), 396000, "", "ID1", "숙박비", "호텔")]
    plans, gaps, missing = fx.plans_from_sheet(settings.DEFAULTS, screen, path, 1)
    assert plans == []  # 계획에 오르지 않는다 = '그대로 둡니다'
    assert not missing  # 짝은 지어졌다. 할 일이 없을 뿐이다


def test_날짜는_Enter로_확정하고_칸을_떠난다():
    """날짜 위젯에서 Escape 는 '취소'다.

    화면 글자는 남아서 우리 확인은 통과하는데, 값은 안 들어가서 저장하면
    Concur가 '날짜 범위' 가 없다고 거부했다 (실측 2026-09-09).
    """
    import inspect

    from src import fix_expenses as fx

    소스 = inspect.getsource(fx._set_date_range)
    assert 'press("Enter")' in 소스 and 'press("Tab")' in 소스
    assert 'press("Escape")' not in 소스


def test_실패해도_어디까지_했는지_알린다():
    """날짜를 넣고 실패한 건지 넣지도 못한 건지 몰라 몇 번을 헤맸다."""
    import inspect

    from src import fix_expenses as fx

    소스 = inspect.getsource(fx._apply_lodging)
    assert "여기까지 했습니다" in 소스
    assert "숙박 날짜 '" in 소스  # 넣는 순간에도 찍는다


def test_연도_없는_날짜는_그_경비의_해로_읽는다():
    """작업지가 날짜를 8/17 로 보여준다. 사람이 그대로 치는 일이 있다.

    연도를 안 주면 1900년이 되고, 그걸 그대로 Concur에 넣으면 아무도
    못 알아챈다.
    """
    from src.sheet import _as_date

    assert _as_date("8/17", 2026) == date(2026, 8, 17)
    assert _as_date("08/17", 2026) == date(2026, 8, 17)
    assert _as_date("2026-08-17", 2026) == date(2026, 8, 17)  # 연도가 있으면 그대로
    assert _as_date("2025-08-17", 2026) == date(2025, 8, 17)  # 힌트가 덮지 않는다
    assert _as_date("", 2026) is None


def test_텍스트로_친_숙박_날짜도_읽는다(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "거래일,금액,승인번호,경비유형,입실날짜,퇴실날짜\n"
        "2026-08-12,396000,A1,숙박비,8/17,8/21\n",
        encoding="utf-8-sig",
    )
    row = sheet.load(path)[0]
    assert row.checkin == date(2026, 8, 17) and row.checkout == date(2026, 8, 21)
    assert row.nights == 4


def test_숙박비인데_못_읽으면_칸의_원본을_보여준다(tmp_path, capsys):
    """비어 있으면 그 파일에 값이 없는 것이고, 뭔가 있는데 못 읽었으면
    우리가 못 읽는 형식이다. 이 구분이 안 돼서 여러 번 돌았다."""
    path = tmp_path / "m.csv"
    path.write_text(
        "거래일,금액,승인번호,경비유형,입실날짜,퇴실날짜\n"
        "2026-08-12,396000,A1,숙박비,8월 17일,\n",
        encoding="utf-8-sig",
    )
    sheet.load(path)
    말 = capsys.readouterr().out
    assert "'8월 17일'" in 말 and "날짜로 읽지 못했습니다" in 말
    assert "작업지 칸:" in 말 and "입실날짜" in 말  # 어떤 칸이 있는지도


def test_엑셀_날짜_일련번호를_읽는다():
    """실측 2026-09-09: 입실 '46251' / 퇴실 '46255'.

    엑셀은 날짜를 '1899-12-30부터 며칠'인 숫자로 저장한다. 셀 서식이 날짜가
    아니면 그 숫자가 그대로 읽힌다. 사람 눈에는 8/17로 보이는데 우리는 못
    읽어서, 날짜 없이 저장하다가 Concur가 거부했다.
    """
    from src.sheet import _as_date

    assert _as_date("46251") == date(2026, 8, 17)
    assert _as_date("46255") == date(2026, 8, 21)
    assert _as_date("46269") == date(2026, 9, 4)
    assert _as_date(46251) == date(2026, 8, 17)
    assert _as_date("46251.0") == date(2026, 8, 17)


def test_아무_숫자나_날짜로_보지_않는다():
    """금액이 잘못 들어와도 날짜로 읽어버리면 안 된다. 2000~2099년만 받는다."""
    from src.sheet import _as_date

    assert _as_date("12345") is None  # 1933년 - 이 프로그램이 다룰 날짜가 아니다
    assert _as_date("999999") is None
    assert _as_date("396000") is None  # 금액이 잘못 들어온 경우


def test_일련번호로_들어온_숙박_날짜가_박수까지_맞는다(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        "거래일,금액,승인번호,경비유형,입실날짜,퇴실날짜\n"
        "2026-08-12,396000,A1,숙박비,46251,46255\n",
        encoding="utf-8-sig",
    )
    row = sheet.load(path)[0]
    assert (row.checkin, row.checkout) == (date(2026, 8, 17), date(2026, 8, 21))
    assert row.nights == 4


def test_탭이_덮이면_30초를_버리지_않는다():
    """실측 2026-09-09: '<span>금액</span> ... subtree intercepts pointer events'

    전체 화면 사이드 패널이 탭을 덮으면 아무리 기다려도 안 눌린다. 30초를
    버리고 Playwright 덤프를 토하느니 무엇이 막았는지 말한다.
    """
    import inspect

    from src import fix_expenses as fx

    소스 = inspect.getsource(fx._open_tab)
    assert "timeout=8000" in 소스
    assert "concur_ui.click_target" in 소스
