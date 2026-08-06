"""파서 회귀 테스트.

fixtures/sample_slip.pdf 는 실제 전표라서 git에 올리지 않는다(.gitignore).
샘플을 그 경로에 두면 테스트가 돌고, 없으면 건너뛴다.
"""

from datetime import datetime
from pathlib import Path

import pytest

from src.slip_parser import parse_slip

SAMPLE = Path(__file__).parent / "fixtures" / "sample_slip.pdf"

pytestmark = pytest.mark.skipif(
    not SAMPLE.exists(), reason="tests/fixtures/sample_slip.pdf 없음"
)


@pytest.fixture(scope="module")
def slip():
    return parse_slip(SAMPLE)


def test_거래일시는_파일명이_아니라_전표_내용에서_나온다(slip):
    # 원본 파일명에는 20260711이 박혀 있지만 실제 거래일은 06/30이다.
    assert slip.transacted_at == datetime(2026, 6, 30, 20, 59, 44)


def test_합계금액(slip):
    assert slip.total == 22900
    assert slip.amount + slip.vat + slip.service_charge == slip.total


def test_승인번호(slip):
    assert slip.approval_no == "00099016"


def test_가맹점(slip):
    assert slip.merchant_name == "나이스-쿠팡"
    assert slip.store_name == "나이스-쿠팡"


def test_카드정보(slip):
    assert slip.card_no == "****-640348-91102"
    assert slip.card_type == "아메리칸익스프레스법인카드(법인리워드형)"


def test_파일명(slip):
    assert slip.filename() == "20260630_22900_00099016.pdf"
