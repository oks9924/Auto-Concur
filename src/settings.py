"""사람이 바꿀 값들. settings.json 에 저장하고 GUI에서 편집한다.

코드에 박아두면 문구 하나 바꾸는 데도 소스를 고쳐야 한다. 반대로 규칙까지
설정으로 빼면 무슨 일이 벌어지는지 알 수 없게 된다. 값만 뺀다.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path("settings.json")

# 경비 유형 코드. Concur 드롭다운 옵션 id에 박혀 있는 값이다
# (예: ':r20r:-list-_-_-LODNG-_-_-0' -> LODNG). 앞부분은 매번 바뀌지만 코드는 고정이다.
# 새 유형이 생기면 `python -m src.fix_expenses --list-types` 로 확인해서 추가한다.
#
# '개인 식사 (SISW Only)'는 일부러 뺐다. 고를 일이 없고, Concur가 그렇게 잡아둔
# 건을 '내부 직원간 식음료'로 바꾸는 것이 우리가 하려는 일이다. 여기서 빼도
# 그 감지는 그대로 된다 - 감지는 화면에 찍힌 이름으로 하지 코드로 하지 않는다.
EXPENSE_TYPE_CODES = {
    "내부 직원간 식음료": "01182",
    "숙박비": "LODNG",
    "대중교통비(지하철, 버스, 기차, 택시, 통행료 등)": "TRAIN",
    "주차비": "PARKG",
    "렌터카비": "CARRT",
    "항공비": "AIRFR",
    "환급 불가 경비/개인 지출": "01000",
    "외부인 포함 식음료 (법인카드 결제건, 또는 3만원 이하)": "01093",
    "외부인 포함 식음료 (3만원 초과 현금 및 개인카드)": "01094",
    "내부 직원간 행사비 (워크샵 등)": "01004",
    "기타 사무용품(문구류 등)": "01143",
    "일반 배송비(택배, 퀵서비스 등)": "01005",
    "법인폰 관련 비용": "CELPH",
    "내부 직원 용 선물": "GIFTS",
}

DEFAULTS = {
    "business_purpose": "현대 중공업 식대",
    "comment": "현대 중공업 출장 식대",
    "attendee_query": "kyungsik.oh",
    # 이 금액 이상이면 large_amount_type 으로 바꾸고 참석자·목적·코멘트는 넣지 않는다.
    "lodging_threshold": 100000,
    "large_amount_type": "숙박비",
    # 영수증 매칭에서 허용할 날짜 차이. 카드 매입 처리 때문에 하루 어긋난다.
    "date_tolerance_days": 1,
    "downloads_dir": "downloads",
    "expense_type_codes": EXPENSE_TYPE_CODES,
}


def load() -> dict:
    """settings.json 을 읽는다. 없거나 빠진 항목은 기본값을 쓴다."""
    data = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{SETTINGS_PATH} 를 읽지 못했습니다: {exc}")
        data.update({k: v for k, v in saved.items() if v is not None})
    return data


def save(data: dict) -> None:
    SETTINGS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def code_for(data: dict, type_name: str) -> str:
    """경비유형 이름으로 코드를 찾는다. 부분 일치도 허용한다."""
    codes = data.get("expense_type_codes", EXPENSE_TYPE_CODES)
    if type_name in codes:
        return codes[type_name]
    for name, code in codes.items():
        if name.startswith(type_name) or type_name in name:
            return code
    raise SystemExit(
        f"경비유형 '{type_name}' 의 코드를 알 수 없습니다. settings.json 의 expense_type_codes 에 "
        "추가하시거나 `python -m src.fix_expenses --list-types` 로 확인해 주세요."
    )
