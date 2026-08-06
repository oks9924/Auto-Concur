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

from playwright.sync_api import Error as PWError
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
# 행 목록은 반드시 한 곳에서만 정의한다. 읽을 때와 누를 때 목록이 다르면
# 인덱스가 어긋나서 엉뚱한 행을 누른다(실제로 합계 행을 눌렀다).
ROWS_FN = """
  const cellsOf = r => [...r.querySelectorAll('[role="gridcell"], [role="cell"]')];
  const gridRows = () => [...document.querySelectorAll('[role="row"]')]
    .filter(r => cellsOf(r).length);
"""

READ_ROWS_JS = (
    "() => {"
    + ROWS_FN
    + """
  return gridRows().map((r, i) => ({
    index: i,
    cells: cellsOf(r).map(c => (c.innerText || '').trim().replace(/\\s+/g, ' ')),
    // 상세로 가는 링크가 있으면 클릭 대신 주소로 바로 간다. 훨씬 덜 깨진다.
    href: (r.querySelector('a[href*="/expenses/"]') || {}).href || null,
  }));
}"""
)

DUMP_ROW_JS = (
    "(i) => {"
    + ROWS_FN
    + """
  const r = gridRows()[i];
  return r ? r.outerHTML.slice(0, 12000) : null;
}"""
)

# 상세 폼이 이 전표의 금액을 보여줄 때까지 기다린다. 대기와 검증을 한 번에 한다.
# SPA라 행을 눌러도 load 이벤트가 안 나므로 네비게이션을 기다리면 안 된다.
WAIT_AMOUNT_JS = """
(expected) => {
  const el = document.querySelector('#transactionAmount');
  return !!el && el.value.replace(/[^0-9]/g, '') === expected;
}
"""

# 행 안의 버튼은 누르면 안 된다. 알림(오류/경고) 버튼과 카드 버튼은 팝오버만
# 열고 상세는 안 열린다. 날짜가 든 셀을 그대로 누른다.
CLICK_ROW_JS = (
    "(i) => {"
    + ROWS_FN
    + """
  const row = gridRows()[i];
  if (!row) return false;
  const cells = cellsOf(row);
  const target = cells.find(c => /\\d{4}-\\d{2}-\\d{2}/.test(c.innerText || ''))
              || cells.find(c => (c.innerText || '').trim() && !c.querySelector('button, input'));
  if (!target) return false;
  target.click();
  return true;
}"""
)

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
    href: str | None = None


def done_path(folder: Path) -> Path:
    return folder / "attached.txt"


def load_done(folder: Path) -> set[str]:
    """이미 붙인 승인번호. 두 번 돌려도 영수증이 겹쳐 붙지 않게 한다."""
    path = done_path(folder)
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_done(folder: Path, approval: str) -> None:
    with done_path(folder).open("a", encoding="utf-8") as f:
        f.write(approval + "\n")


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


def _eval(page, script: str, arg=None, tries: int = 4):
    """Concur는 화면을 계속 다시 그린다. 렌더 도중에 evaluate하면 실행 컨텍스트가
    날아가면서 'Execution context was destroyed'가 난다. 잠깐 기다렸다 다시 한다.
    """
    last = None
    for _ in range(tries):
        try:
            page.wait_for_load_state("domcontentloaded")
            return page.evaluate(script) if arg is None else page.evaluate(script, arg)
        except PWError as exc:
            if "Execution context was destroyed" not in str(exc):
                raise
            last = exc
            page.wait_for_timeout(2000)
    raise AttachError(f"페이지가 계속 바뀌어서 읽지 못했다. 화면이 멈춘 뒤 다시 해라: {last}")


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
    for raw in _eval(page, READ_ROWS_JS):
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
            Row(index=raw["index"], when=when, amount=amount, text=joined[:90], href=raw.get("href"))
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


def open_expense(page, slip: Slip, row: Row, folder: Path) -> None:
    """상세 화면을 열고, 이 전표의 금액이 보일 때까지 기다린다.

    SPA라 행을 눌러도 load 이벤트가 안 난다. 네비게이션이 아니라 '#transactionAmount가
    이 금액이 되는 것'을 기다린다. 대기와 '맞는 경비를 열었나' 확인이 한 번에 된다.
    """
    if row.href:
        page.goto(row.href, wait_until="domcontentloaded")
    else:
        _eval(page, CLICK_ROW_JS, row.index)

    try:
        page.wait_for_function(WAIT_AMOUNT_JS, arg=str(slip.amount), timeout=20000)
    except PWTimeout:
        try:
            shown = page.input_value(AMOUNT_FIELD, timeout=2000)
        except (PWTimeout, PWError):
            shown = None
        dump = folder / "concur-row.html"
        if not dump.exists():
            html = _eval(page, DUMP_ROW_JS, row.index)
            if html:
                dump.write_text(html, encoding="utf-8")
                print(f"     행 HTML 저장: {dump}")
        raise AttachError(
            f"상세 화면에서 {slip.amount:,}원을 확인하지 못했다"
            + (f" (화면 금액: {shown})" if shown else " (금액 필드가 없다 - 상세가 안 열렸다)")
            + ". 첨부하지 않았다."
        )


def attach(page, slip: Slip, row: Row, folder: Path) -> None:
    open_expense(page, slip, row, folder)
    page.set_input_files(UPLOAD_INPUT, str(slip.path))
    page.wait_for_timeout(3000)
    page.get_by_role("button", name="경비 저장").first.click()
    page.wait_for_timeout(2000)


def run(folder: Path, apply: bool, tolerance: int, limit: int | None) -> int:
    slips = load_manifest(folder)
    done = load_done(folder)
    if done:
        slips = [s for s in slips if s.approval not in done]
        print(f"이미 붙인 {len(done)}건은 건너뛴다. 남은 전표 {len(slips)}건.")
    if not slips:
        print("붙일 것이 없다.")
        return 0

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

        page.wait_for_timeout(2000)  # Enter 직후에도 화면을 더 그린다
        report_url = page.url
        rows = read_rows(page)
        if not rows:
            dump = folder / "concur-dump.html"
            try:
                dump.write_text(page.content(), encoding="utf-8")
            except PWError:
                dump = None
            raise AttachError(
                "경비 목록 행을 읽지 못했다."
                + (f" 화면 HTML을 {dump} 에 남겼다." if dump else "")
            )

        dated = [r for r in rows if r.when and r.amount]
        linked = sum(1 for r in dated if r.href)
        print(f"\n목록 {len(rows)}행 중 날짜·금액을 읽은 행 {len(dated)}개, 전표 {len(slips)}건")
        print(f"상세 링크가 있는 행 {linked}개" + ("" if linked == len(dated) else " (나머지는 클릭으로 연다)"))

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
            print("처음에는 --apply --limit 1 로 한 건만 해보고 Concur에서 확인해라.")
            ctx.close()
            return 0

        if limit:
            pairs = pairs[:limit]
            print(f"\n--limit {limit} 이므로 {len(pairs)}건만 붙인다.")

        attached, failed = 0, []
        for i, (slip, row) in enumerate(pairs, 1):
            try:
                if not row.href:  # 링크가 있으면 목록을 거칠 필요가 없다
                    page.goto(report_url, wait_until="domcontentloaded")
                    page.wait_for_timeout(2000)
                attach(page, slip, row, folder)
                mark_done(folder, slip.approval)
                attached += 1
                print(f"  [{i}/{len(pairs)}] 첨부 {slip.path.name}")
            except (AttachError, PWTimeout) as exc:
                failed.append((slip, str(exc)))
                print(f"  [{i}/{len(pairs)}] 실패 {slip.path.name}: {exc}")

        ctx.close()

    print(f"\n{attached}건 첨부")
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
    ap.add_argument("--limit", type=int, help="앞에서 N건만 (동작 확인용)")
    args = ap.parse_args()
    try:
        return run(args.dir, args.apply, args.tolerance, args.limit)
    except AttachError as exc:
        print(f"\n중단: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
