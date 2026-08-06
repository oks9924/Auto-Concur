"""Concur 경비에 전표 PDF를 첨부한다.

로그인(SSO)과 리포트 열기는 사람이 한다. 스크립트는 열려 있는 리포트의
경비 목록을 읽어서 manifest.csv와 맞춰보고, 확실한 것만 첨부한다.

    python -m src.attach_receipts                 # 매칭 계획만 출력 (아무것도 안 바꿈)
    python -m src.attach_receipts --apply         # 실제로 첨부

기본은 계획 출력이다. 잘못 붙인 전표는 감사에서 문제가 되므로 사람이 먼저 본다.

매칭 규칙: 금액이 정확히 같고 거래일이 ±1일 안. 후보가 정확히 하나일 때만
붙인다. 없거나 둘 이상이면 건너뛰고 사람에게 넘긴다. 카드 매입 처리 때문에
Concur 날짜가 거래일과 하루 어긋날 수 있어서 ±1일을 둔다.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import console

PROFILE_DIR = Path("browser-profile") / "concur"
START_URL = "https://travel.siemens.cloud"

UPLOAD_INPUT = "#upload-file"
AMOUNT_FIELD = "#transactionAmount"
DATE_FIELD = "#transactionDate-date-input-field-input"
VENDOR_FIELD = "#vendorName"

# Concur 목록은 role=row / role=gridcell 을 쓴다. 접근성 표준이라 난독화된
# 클래스명(sapcnqr-...-b8e75c)보다 오래 간다.
READ_ROWS_JS = """
() => [...document.querySelectorAll('[role="row"]')]
  .map((r, i) => ({
    index: i,
    cells: [...r.querySelectorAll('[role="gridcell"], [role="cell"]')]
      .map(c => (c.innerText || '').trim().replace(/\\s+/g, ' ')),
  }))
  .filter(r => r.cells.length)
"""

# 행을 클릭해 상세로 들어가는 방법을 모른다. 행 안에서 누를 만한 것을 찾아 누른다.
CLICK_ROW_JS = """
(i) => {
  const rows = [...document.querySelectorAll('[role="row"]')]
    .filter(r => r.querySelectorAll('[role="gridcell"], [role="cell"]').length);
  const row = rows[i];
  if (!row) return false;
  const cells = [...row.querySelectorAll('[role="gridcell"], [role="cell"]')];
  for (const c of cells) {
    const link = c.querySelector('a, button:not([class*="checkbox"])');
    if (link && (link.innerText || '').trim()) { link.click(); return true; }
  }
  cells[1]?.click() ?? row.click();
  return true;
}
"""

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
AMOUNT_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


class AttachError(Exception):
    """추측으로 진행하면 안 되는 상태."""


@dataclass
class Slip:
    path: Path
    when: date
    amount: int
    merchant: str
    approval: str


@dataclass
class Row:
    index: int
    when: date | None
    amount: int | None
    text: str
    has_receipt: bool


def load_manifest(folder: Path) -> list[Slip]:
    path = folder / "manifest.csv"
    if not path.exists():
        raise AttachError(f"manifest.csv가 없다: {path}. 먼저 src.organize 를 돌려라.")
    slips = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            pdf = folder / r["파일명"]
            if not pdf.exists():
                raise AttachError(f"manifest에 있는 파일이 없다: {pdf}")
            slips.append(
                Slip(
                    path=pdf,
                    when=datetime.strptime(r["거래일"], "%Y-%m-%d").date(),
                    amount=int(r["합계"]),
                    merchant=r["가맹점명"],
                    approval=r["승인번호"],
                )
            )
    return slips


def _parse_amount(text: str) -> int | None:
    cleaned = text.replace("원", "").replace("KRW", "").strip()
    if not AMOUNT_RE.match(cleaned):
        return None
    try:
        return int(round(float(cleaned.replace(",", ""))))
    except ValueError:
        return None


def read_rows(page) -> list[Row]:
    rows = []
    for raw in page.evaluate(READ_ROWS_JS):
        cells = raw["cells"]
        joined = " | ".join(cells)
        when = None
        amount = None
        for cell in cells:
            if when is None:
                m = DATE_RE.search(cell)
                if m:
                    when = date(*(int(g) for g in m.groups()))
            if amount is None:
                got = _parse_amount(cell)
                # 금액 칼럼만 잡는다. 날짜에서 떼어낸 숫자가 섞이지 않게 한다.
                if got is not None and got >= 100:
                    amount = got
        rows.append(
            Row(
                index=raw["index"],
                when=when,
                amount=amount,
                text=joined[:90],
                has_receipt="영수증 없음" not in joined,
            )
        )
    return rows


def match(slips: list[Slip], rows: list[Row], tolerance_days: int) -> tuple[list, list]:
    """(확정 매칭, 건너뛴 것). 후보가 정확히 하나일 때만 확정한다."""
    pairs, skipped = [], []
    used: set[int] = set()
    for slip in slips:
        cands = [
            r
            for r in rows
            if r.index not in used
            and r.amount == slip.amount
            and r.when is not None
            and abs((r.when - slip.when).days) <= tolerance_days
        ]
        if len(cands) == 1:
            used.add(cands[0].index)
            pairs.append((slip, cands[0]))
        else:
            skipped.append((slip, "후보 없음" if not cands else f"후보 {len(cands)}개 - 모호함"))
    return pairs, skipped


def attach(page, slip: Slip, row: Row) -> None:
    before = page.url
    page.evaluate(CLICK_ROW_JS, row.index)
    page.wait_for_url(lambda u: u != before, timeout=15000)
    page.wait_for_selector(AMOUNT_FIELD, timeout=15000)

    # 엉뚱한 경비를 열었을 수 있다. 붙이기 전에 화면 값으로 다시 확인한다.
    shown_amount = _parse_amount(page.input_value(AMOUNT_FIELD))
    if shown_amount != slip.amount:
        raise AttachError(
            f"열린 경비의 금액({shown_amount})이 전표({slip.amount})와 다르다. 첨부하지 않았다."
        )

    page.set_input_files(UPLOAD_INPUT, str(slip.path))
    page.wait_for_timeout(3000)
    page.get_by_role("button", name="경비 저장").first.click()
    page.wait_for_timeout(2000)


def run(folder: Path, apply: bool, tolerance: int) -> int:
    slips = load_manifest(folder)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=False, accept_downloads=True
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(START_URL, wait_until="domcontentloaded")

        print("\n" + "=" * 64)
        print("  Concur에 SSO로 로그인하고, 처리할 경비 리포트를 열어라.")
        print("  경비 목록이 보이는 상태에서 Enter를 눌러라.")
        print("=" * 64)
        console.wait_enter("리포트를 열었으면 Enter > ")

        report_url = page.url
        rows = read_rows(page)
        if not rows:
            (folder / "concur-dump.html").write_text(page.content(), encoding="utf-8")
            raise AttachError(
                f"경비 목록 행을 읽지 못했다. 화면 HTML을 {folder / 'concur-dump.html'} 에 남겼다."
            )

        dated = [r for r in rows if r.when and r.amount]
        print(f"\n목록 {len(rows)}행 중 날짜·금액을 읽은 행 {len(dated)}개, 전표 {len(slips)}건")

        pairs, skipped = match(slips, dated, tolerance)
        print(f"\n확정 매칭 {len(pairs)}건:")
        for slip, row in pairs:
            gap = (row.when - slip.when).days
            mark = "" if gap == 0 else f"  (Concur 날짜 {gap:+d}일)"
            print(f"  {slip.when} {slip.amount:>9,}원  {slip.merchant[:16]:16} -> {row.text[:50]}{mark}")

        if skipped:
            print(f"\n건너뜀 {len(skipped)}건 (사람이 처리):")
            for slip, why in skipped:
                print(f"  {slip.when} {slip.amount:>9,}원  {slip.merchant[:16]:16} - {why}")

        if not apply:
            print("\n계획만 출력했다. 실제로 붙이려면 --apply 를 붙여라.")
            ctx.close()
            return 0

        done, failed = 0, []
        for i, (slip, row) in enumerate(pairs, 1):
            try:
                page.goto(report_url, wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
                attach(page, slip, row)
                done += 1
                print(f"  [{i}/{len(pairs)}] 첨부 {slip.path.name}")
            except (AttachError, PWTimeout) as exc:
                failed.append((slip, str(exc)))
                print(f"  [{i}/{len(pairs)}] 실패 {slip.path.name}: {exc}")

        ctx.close()

    print(f"\n{done}건 첨부")
    if failed:
        print(f"실패 {len(failed)}건:")
        for slip, why in failed:
            print(f"  ! {slip.path.name}: {why}")
        return 1
    return 0


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="Concur 경비에 전표 첨부")
    ap.add_argument("--dir", type=Path, default=Path("downloads"), help="manifest.csv가 있는 폴더")
    ap.add_argument("--apply", action="store_true", help="실제로 첨부한다")
    ap.add_argument("--tolerance", type=int, default=1, help="Concur 날짜 허용 오차(일)")
    args = ap.parse_args()
    try:
        return run(args.dir, args.apply, args.tolerance)
    except AttachError as exc:
        print(f"\n중단: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
