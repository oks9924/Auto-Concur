"""D단계 규칙 회귀 테스트.

경비유형을 잘못 바꾸면 회계 처리가 틀어진다. 규칙을 여기서 고정한다.
"""

from datetime import date

from src.attach_receipts import Row
from src import settings
from src.fix_expenses import decide, rules_from

RULES = rules_from(settings.DEFAULTS)
CODE_MEAL = settings.EXPENSE_TYPE_CODES["내부 직원간 식음료"]
CODE_LODGING = settings.EXPENSE_TYPE_CODES["숙박비"]

MEAL = "내부 직원간 식음료 (점심, 야근식대, 부서회식, 음료 등)"


def row(amount: int, kind: str) -> Row:
    return Row(0, date(2026, 7, 1), amount, "", "EXPENSE_ID", kind, "가맹점")


def test_십만원_이상은_숙박비로():
    plan = decide(row(180_000, MEAL), RULES)
    assert plan.type_code == CODE_LODGING


def test_십만원_이상에는_참석자를_넣지_않는다():
    # 숙박은 식대가 아니다.
    assert decide(row(180_000, MEAL), RULES).fill_meal is False


def test_이미_숙박비면_건드리지_않는다():
    assert decide(row(711_620, "숙박비"), RULES) is None


def test_개인_식사는_내부_직원간_식음료로_바꾸고_다_채운다():
    plan = decide(row(75_400, "개인 식사 (SISW Only)"), RULES)
    assert plan.type_code == CODE_MEAL
    assert plan.fill_meal is True


def test_이미_내부_직원간_식음료면_유형은_두고_채우기만():
    plan = decide(row(19_800, MEAL), RULES)
    assert plan.type_code is None
    assert plan.fill_meal is True


def test_환급_불가는_건드리지_않는다():
    assert decide(row(7_900, "환급 불가 경비/개인 지출"), RULES) is None


def test_모르는_유형은_건드리지_않는다():
    # 대중교통비·주차비 등은 규칙에 없다. 손대지 않는 쪽이 안전하다.
    assert decide(row(5_000, "대중교통비(지하철, 버스, 기차, 택시, 통행료 등)"), RULES) is None


def test_십만원_규칙이_개인_식사보다_우선한다():
    plan = decide(row(150_000, "개인 식사 (SISW Only)"), RULES)
    assert plan.type_code == CODE_LODGING and plan.fill_meal is False
