"""사람이 찍은 영수증 사진.

전표 PDF는 안에 글자가 있어서 값을 뽑을 수 있다. 사진은 그런 게 없으므로
파일 이름이 유일한 근거다. 그래서 이름 규칙이 곧 계약이고, 읽을 수 없는
이름은 짐작하지 않고 멈춘다.
"""

from datetime import date
from pathlib import Path

import pytest

from src import photo_slip


@pytest.mark.parametrize(
    "name,when,amount",
    [
        ("20260809-17000.jpg", date(2026, 8, 9), 17000),
        ("2026-08-09_17000.png", date(2026, 8, 9), 17000),
        ("20260809 17,000.jpeg", date(2026, 8, 9), 17000),
        ("2026.08.09-1500.PNG", date(2026, 8, 9), 1500),
        ("20260809-17000-2.jpg", date(2026, 8, 9), 17000),  # 같은 날 같은 금액 둘째 장
    ],
)
def test_이름에서_날짜와_금액을_읽는다(name, when, amount):
    got_when, got_amount, key = photo_slip.parse(Path(name))
    assert (got_when, got_amount) == (when, amount)
    assert key == name  # 파일 이름이 승인번호 자리를 대신한다


@pytest.mark.parametrize(
    "name",
    [
        "영수증.jpg",  # 아무 정보도 없다
        "20260809.jpg",  # 금액이 없다
        "17000.jpg",  # 날짜가 없다
        "20261332-17000.jpg",  # 없는 날짜
        "20260809-0.jpg",  # 0원
        "IMG_1234.jpg",  # 카메라가 붙인 이름
    ],
)
def test_못_읽는_이름은_멈춘다(name):
    """짐작해서 엉뚱한 경비에 붙이면 감사에서 설명해야 한다."""
    with pytest.raises(photo_slip.PhotoNameError) as err:
        photo_slip.parse(Path(name))
    assert "20260809-17000.jpg" in str(err.value)  # 어떻게 고치는지 알려준다


def test_HEIC는_받지_않는다():
    """아이폰 기본 형식이지만 Concur가 안 받는다.

    붙는 줄 알고 넘어가면 나중에 반려된다. 아예 대상에서 뺀다.
    """
    assert not photo_slip.is_photo(Path("20260809-17000.heic"))
    assert photo_slip.is_photo(Path("20260809-17000.jpg"))
    assert not photo_slip.is_photo(Path("manifest.csv"))


def test_사진과_전표가_한_작업지에_들어간다(tmp_path):
    from src.organize import MANIFEST_COLUMNS, organize

    (tmp_path / "20260809-17000.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "20260810-3,500.png").write_bytes(b"\x89PNG")
    organize(tmp_path, apply=False)

    from src import sheet

    rows = sheet.load(tmp_path / "manifest.csv")
    assert [(r.when, r.amount) for r in rows] == [
        (date(2026, 8, 9), 17000),
        (date(2026, 8, 10), 3500),
    ]
    assert set(MANIFEST_COLUMNS) >= {"파일명", "거래일", "금액", "승인번호"}


def test_사진은_이름을_바꾸지_않는다(tmp_path):
    """이름이 곧 승인번호 자리다. 바꾸면 사람이 적어둔 값과 첨부 기록을 잃는다."""
    from src.organize import organize

    photo = tmp_path / "20260809-17000.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    organize(tmp_path, apply=True)
    assert photo.exists()


def test_적어둔_값은_다시_돌려도_남는다(tmp_path):
    """PDF와 같은 규칙이다. 열쇠가 승인번호 대신 파일 이름일 뿐이다."""
    import csv

    from src.organize import MANIFEST_COLUMNS, organize

    photo = tmp_path / "20260809-17000.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    organize(tmp_path, apply=True)

    path = tmp_path / "manifest.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    rows[0]["코멘트"] = "팀 점심"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        w.writeheader()
        w.writerows(rows)

    organize(tmp_path, apply=True)
    again = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert again[0]["코멘트"] == "팀 점심"


def test_이름이_틀린_사진은_짚어준다(tmp_path, capsys):
    from src.organize import organize

    (tmp_path / "IMG_1234.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "20260809-17000.jpg").write_bytes(b"\xff\xd8\xff")
    assert organize(tmp_path, apply=False) == 1  # 하나라도 못 읽으면 실패로 알린다

    말 = capsys.readouterr()
    assert "IMG_1234.jpg" in 말.err
    assert "20260809-17000.jpg" in 말.err  # 어떻게 고치는지
