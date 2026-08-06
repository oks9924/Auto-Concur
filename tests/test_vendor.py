"""가맹점 판별과 설정 조회 테스트.

manifest는 한글, Concur는 로마자라 옮겨서 견준다. 잘못 갈리면 엉뚱한 경비에
영수증이 붙으므로 실측으로 확인한 값을 여기에 고정한다.
"""

import pytest

from src import settings
from src.attach_receipts import VENDOR_MARGIN, VENDOR_MIN
from src.hangul import romanize, similarity

# 실제 화면에서 확인한 쌍
SAME = [
    ("라한호텔울산", "RA HAN HO TEL UL SAN"),
    ("쿠우쿠우울산동구점", "KU WOO KU WOO UL SAN DONG GU JEOM"),
]
DIFFERENT = [
    ("라한호텔울산", "KU WOO KU WOO UL SAN DONG GU JEOM"),
    ("쿠우쿠우울산동구점", "RA HAN HO TEL UL SAN"),
    ("네이버파이낸셜(주)-트립닷컴", "RA HAN HO TEL UL SAN"),
]


def test_로마자로_옮긴다():
    assert romanize("라한호텔울산") == "rahanhotelulsan"


@pytest.mark.parametrize("korean,latin", SAME)
def test_같은_가게는_문턱을_넘는다(korean, latin):
    assert similarity(korean, latin) >= VENDOR_MIN


@pytest.mark.parametrize("korean,latin", DIFFERENT)
def test_다른_가게는_문턱을_못_넘는다(korean, latin):
    assert similarity(korean, latin) < VENDOR_MIN


def test_같은_가게와_다른_가게_사이가_충분히_벌어진다():
    # 이 간격이 좁으면 후보 여럿일 때 잘못 고른다.
    best = min(similarity(k, l) for k, l in SAME)
    worst = max(similarity(k, l) for k, l in DIFFERENT)
    assert best - worst >= VENDOR_MARGIN


def test_한글이_아니면_0이다():
    assert similarity("", "RA HAN HO TEL") == 0.0


def test_경비유형_코드를_이름으로_찾는다():
    cfg = settings.DEFAULTS
    assert settings.code_for(cfg, "숙박비") == "LODNG"
    assert settings.code_for(cfg, "내부 직원간 식음료") == "01182"


def test_택시는_대중교통비에_들어_있다():
    # 새 유형이 필요할까 싶지만 택시는 이미 자리가 있다.
    code = settings.code_for(settings.DEFAULTS, "택시")
    assert code == "TRAIN"
