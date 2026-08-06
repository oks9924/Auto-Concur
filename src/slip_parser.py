"""현대카드 '카드매출전표 인터넷 재발급용' PDF에서 거래 정보를 뽑는다.

이 전표는 폼 라벨이 배경 JPEG에 그려져 있고, 값만 텍스트 레이어에 들어 있다.
그래서 '거래일시:' 같은 라벨 문자열로는 값을 찾을 수 없다. 대신 고정 그리드
좌표로 셀을 읽는다. 좌표는 596x843pt 페이지 기준이고 행 간격은 27.68pt로 일정하다.

값이 확실하지 않으면 추측하지 않고 SlipParseError를 던진다. 잘못 읽은 전표가
엉뚱한 경비에 첨부되는 것이 파싱 실패보다 훨씬 나쁘기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pdfplumber

ROW0_TOP = 126.8
ROW_HEIGHT = 27.68
ROW_TOLERANCE = 12.0
COLUMN_SPLIT_X = 290.0

# (왼쪽 라벨, 오른쪽 라벨). 오른쪽이 None이면 전체 폭을 쓰는 행이다.
ROWS: list[tuple[str, str | None]] = [
    ("전표번호", None),
    ("처리일련번호", "카드종류"),
    ("카드번호", "거래일시"),
    ("취소시당초거래일", "유효기간"),
    ("거래유형", "품명"),
    ("결제방법", "매장명"),
    ("판매자", "은행확인"),
    ("대표자", "승인번호"),
    ("가맹점명", "거래고유번호"),
    ("가맹점번호", "사업자등록번호"),
    ("문의전화", "서명"),
    ("가맹점주소", None),
]

# 하단 금액 블록: 금액 / 부가세 / 봉사료 / 합계 순으로 4줄.
AMOUNT_BAND = (470.0, 620.0)
NUMERIC = re.compile(r"^-?[\d,]+$")
DATETIME = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s*(\d{2}):(\d{2}):(\d{2})")


class SlipParseError(Exception):
    """전표에서 필요한 값을 확신할 수 없을 때. 조용히 넘어가지 않는다."""


@dataclass
class Slip:
    source: str
    slip_no: str
    card_type: str
    card_no: str
    transacted_at: datetime
    tx_type: str
    store_name: str
    approval_no: str
    merchant_name: str
    merchant_biz_no: str
    amount: int
    vat: int
    service_charge: int
    total: int

    @property
    def date(self) -> str:
        return self.transacted_at.strftime("%Y%m%d")

    def filename(self) -> str:
        """날짜_금액_승인번호.pdf

        승인번호를 넣는 이유: 같은 날 같은 금액의 결제가 둘 이상일 때
        (커피 두 잔 같은 경우) 날짜+금액만으로는 파일명이 충돌한다.
        """
        return f"{self.date}_{self.total}_{self.approval_no}.pdf"


def _row_index(top: float) -> int | None:
    idx = round((top - ROW0_TOP) / ROW_HEIGHT)
    if not 0 <= idx < len(ROWS):
        return None
    if abs(top - (ROW0_TOP + idx * ROW_HEIGHT)) > ROW_TOLERANCE:
        return None
    return idx


def _join(words: list[dict]) -> str:
    """셀 안의 단어들을 줄 단위로 합친다.

    같은 줄은 공백으로, 줄바꿈은 공백 없이 잇는다. 한글은 단어 중간에서
    줄바꿈되므로('아메리칸익스프레스법인카' + '드(법인리워드형)') 공백을 넣으면 안 된다.
    """
    lines: dict[float, list[dict]] = {}
    for w in words:
        lines.setdefault(round(w["top"], 1), []).append(w)
    out = []
    for top in sorted(lines):
        out.append(" ".join(w["text"] for w in sorted(lines[top], key=lambda w: w["x0"])))
    return "".join(out)


def _read_cells(words: list[dict]) -> dict[str, str]:
    cells: dict[str, list[dict]] = {}
    for w in words:
        if w["top"] >= AMOUNT_BAND[0]:
            continue
        idx = _row_index(w["top"])
        if idx is None:
            continue
        left, right = ROWS[idx]
        label = left if (right is None or w["x0"] < COLUMN_SPLIT_X) else right
        cells.setdefault(label, []).append(w)
    return {label: _join(ws) for label, ws in cells.items()}


def _read_amounts(words: list[dict]) -> tuple[int, int, int, int]:
    lo, hi = AMOUNT_BAND
    found = [w for w in words if lo < w["top"] < hi and NUMERIC.match(w["text"])]
    if len(found) != 4:
        raise SlipParseError(
            f"금액 블록에서 숫자 4개(금액/부가세/봉사료/합계)를 찾지 못했다: {len(found)}개"
        )
    values = [int(w["text"].replace(",", "")) for w in sorted(found, key=lambda w: w["top"])]
    amount, vat, service_charge, total = values
    if amount + vat + service_charge != total:
        raise SlipParseError(
            f"금액 합이 맞지 않는다: {amount} + {vat} + {service_charge} != {total}"
        )
    return amount, vat, service_charge, total


def parse_slip(path: Path) -> Slip:
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != 1:
            raise SlipParseError(
                f"1페이지 전표를 기대했는데 {len(pdf.pages)}페이지다. "
                "한 PDF에 여러 전표가 들어 있으면 파서를 고쳐야 한다."
            )
        words = pdf.pages[0].extract_words()

    if not words:
        raise SlipParseError("텍스트 레이어가 비어 있다. 스캔본이면 OCR이 필요하다.")

    cells = _read_cells(words)

    raw_dt = cells.get("거래일시", "")
    m = DATETIME.search(raw_dt)
    if not m:
        raise SlipParseError(f"거래일시를 읽지 못했다: {raw_dt!r}")
    y, mo, d, h, mi, s = (int(g) for g in m.groups())
    transacted_at = datetime(y, mo, d, h, mi, s)

    approval_no = cells.get("승인번호", "")
    if not approval_no:
        raise SlipParseError("승인번호가 비어 있다")

    amount, vat, service_charge, total = _read_amounts(words)

    return Slip(
        source=str(path),
        slip_no=cells.get("전표번호", ""),
        card_type=cells.get("카드종류", ""),
        card_no=cells.get("카드번호", ""),
        transacted_at=transacted_at,
        tx_type=cells.get("거래유형", ""),
        store_name=cells.get("매장명", ""),
        approval_no=approval_no,
        merchant_name=cells.get("가맹점명", ""),
        merchant_biz_no=cells.get("사업자등록번호", ""),
        amount=amount,
        vat=vat,
        service_charge=service_charge,
        total=total,
    )
