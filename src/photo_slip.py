"""사람이 찍은 영수증 사진을 파일 이름으로 읽는다.

전표 PDF는 안에 글자가 있어서 날짜·금액·승인번호를 뽑을 수 있다. 사진은 그런
게 없으므로 파일 이름이 유일한 근거다. 그래서 이름 규칙이 곧 계약이다.

    20260809-17000.jpg      날짜-금액
    2026-08-09_17000.png    구분자와 날짜 모양은 편한 대로
    20260809-17000-2.jpg    같은 날 같은 금액이 둘이면 뒤에 번호를 붙인다

읽을 수 없는 이름은 넘기지 않고 멈춘다. 짐작해서 엉뚱한 경비에 붙이면 감사에서
설명해야 하고, 그건 사진 몇 장 손으로 올리는 것보다 훨씬 비싸다.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Concur 영수증이 받아주는 것들. HEIC는 빼둔다 - 아이폰 기본 형식이지만
# Concur가 안 받아서, 붙는 줄 알고 넘어가면 나중에 반려된다.
SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff"}

# 날짜는 앞에서, 금액은 그 다음 숫자 덩어리에서. 날짜 구분자는 있어도 없어도 된다.
NAME_RE = re.compile(
    r"^(?P<year>\d{4})[-_.]?(?P<month>\d{2})[-_.]?(?P<day>\d{2})"  # 20260809 / 2026-08-09
    r"[-_. ]+"
    r"(?P<amount>[\d,]+)"  # 17000 / 17,000
    r"(?:[-_. ]+(?P<seq>\d+))?$"  # 같은 날 같은 금액이 둘일 때의 꼬리번호
)


class PhotoNameError(Exception):
    """파일 이름에서 날짜·금액을 확신할 수 없을 때."""


HOWTO = "이름을 '날짜-금액' 으로 바꿔 주세요 (예: 20260809-17000.jpg)"


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in SUFFIXES


def parse(path: Path):
    """(거래일, 금액, 승인번호 대신 쓸 키)를 준다.

    승인번호가 없으므로 파일 이름 자체를 키로 쓴다. 다시 돌릴 때 사람이 엑셀에
    적어둔 값을 되찾는 열쇠이고, 이미 붙였는지 기억하는 열쇠이기도 하다.
    그래서 이름을 바꾸면 안 된다 - 사진은 이름을 바꾸지 않는 이유가 이것이다.
    """
    m = NAME_RE.match(path.stem.strip())
    if not m:
        raise PhotoNameError(f"파일 이름에서 날짜와 금액을 읽지 못했습니다. {HOWTO}")

    try:
        when = datetime(int(m["year"]), int(m["month"]), int(m["day"])).date()
    except ValueError:
        raise PhotoNameError(f"'{m['year']}-{m['month']}-{m['day']}' 는 없는 날짜입니다. {HOWTO}") from None

    amount = int(m["amount"].replace(",", ""))
    if amount <= 0:
        raise PhotoNameError(f"금액이 {amount} 입니다. {HOWTO}")

    return when, amount, path.name
