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
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import console, hangul, settings

PROFILE_DIR = Path("browser-profile") / "concur"
START_URL = "https://travel.siemens.cloud"

UPLOAD_INPUT = "#upload-file"
AMOUNT_FIELD = "#transactionAmount"
DATE_FIELD = "#transactionDate-date-input-field-input"
VENDOR_FIELD = "#vendorName"

# 행의 id가 곧 경비 ID다. 상세 주소가 .../reports/{리포트}/expenses/{경비} 라서
# 주소를 직접 만들 수 있다. 클릭해서 여는 것보다 훨씬 확실하다.
#
# data-testid="data-row" 로 데이터 행만 고른다. role=row 만 보면 헤더와 합계
# 행까지 섞여서 인덱스가 어긋난다(실제로 합계 행을 눌렀었다).
# 값은 data-nuiexp 훅에서 직접 읽는다. 칼럼 순서를 짐작하지 않아도 된다.
ROWS_FN = """
  const gridRows = () => [...document.querySelectorAll('[role="row"][data-testid="data-row"]')];
"""

READ_ROWS_JS = (
    "() => {"
    + ROWS_FN
    + """
  const pick = (r, hook) => {
    const el = r.querySelector('[data-nuiexp="' + hook + '"]');
    return el ? (el.innerText || '').trim().replace(/\\s+/g, ' ') : '';
  };
  // 영수증이 이미 붙어 있는지. 영수증 칸을 찾고, 그 안에 아이콘/버튼이 있으면
  // 붙은 것으로 본다. 칸 자체를 못 찾으면 null - '없다'가 아니라 '모른다'다.
  // 모를 때 붙은 것으로 치면 조용히 빠뜨리게 되므로 그때는 평소대로 진행한다.
  const receipt = (r) => {
    const cell = r.querySelector(
      '[data-nuiexp*="receipt" i], [data-testid*="receipt" i], [class*="receipt" i]'
    );
    if (!cell) return null;
    return !!cell.querySelector('img, svg, button, a');
  };
  return gridRows().map((r, i) => ({
    index: i,
    id: r.id || r.getAttribute('data-row-key') || null,
    date: pick(r, 'date-cell'),
    amount: pick(r, 'amount-cell'),
    vendor: pick(r, 'vendor-name'),
    expenseType: pick(r, 'expense-type-cell'),
    receipt: receipt(r),
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

def expense_url(report_url: str, expense_id: str) -> str:
    """리포트 주소에서 경비 상세 주소를 만든다."""
    base = report_url.split("/expenses/")[0].split("?")[0].rstrip("/")
    return f"{base}/expenses/{expense_id}"

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
    expense_id: str | None = None
    expense_type: str = ""
    vendor: str = ""
    has_receipt: bool | None = None  # None이면 화면에서 알 수 없었다는 뜻


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
        raise AttachError(f"manifest.csv가 없습니다: {path}\n먼저 B단계(파싱 · 작업지 생성)를 실행해 주세요.")
    slips = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            pdf = folder / r["파일명"]
            if not pdf.exists():
                raise AttachError(f"작업지에 적힌 전표 파일이 없습니다: {pdf}\nB단계를 --apply 로 다시 실행해 주세요.")
            slips.append(
                Slip(
                    path=pdf,
                    when=datetime.strptime(r["거래일"], "%Y-%m-%d").date(),
                    # '합계'는 옛 이름이다. 예전에 만든 manifest도 그대로 읽힌다.
                    amount=int(r.get("금액") or r["합계"]),
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
    raise AttachError(f"화면이 계속 바뀌어서 읽지 못했습니다. 화면이 멈춘 뒤 다시 시도해 주세요: {last}")


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
        m = DATE_RE.search(raw["date"])
        when = date(*(int(g) for g in m.groups())) if m else None
        label = " ".join(x for x in (raw["expenseType"], raw["vendor"], raw["amount"]) if x)
        rows.append(
            Row(
                index=raw["index"],
                when=when,
                amount=_parse_amount(raw["amount"]),
                text=label[:80],
                expense_id=raw["id"],
                expense_type=raw["expenseType"],
                vendor=raw["vendor"],
                has_receipt=raw.get("receipt"),
            )
        )
    return rows


# 가맹점 유사도 판정. 실측으로 같은 가게는 1.00, 다른 가게는 0.3 미만이었다.
VENDOR_MIN = 0.6  # 이보다 낮으면 같은 가게로 보지 않는다
VENDOR_MARGIN = 0.15  # 2등과 이만큼 벌어져야 확실하다고 본다


def match(slips: list[Slip], rows: list[Row], tolerance_days: int) -> tuple[list, list]:
    """(확정 매칭, 건너뛴 것).

    날짜와 금액이 같은 후보가 여럿이면 가맹점명으로 가른다. manifest는 한글,
    Concur는 로마자라서 옮겨서 견준다. 그것도 갈리지 않으면 앞에서부터
    순서대로 배정한다 — 날짜와 금액이 같으면 어느 쪽이든 된다고 보기로 했다.

    각 매칭은 (전표, 행, 어떻게 정했는지) 세 값이다.
    """
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
        if not cands:
            skipped.append((slip, "후보 없음"))
            continue

        chosen, how = cands[0], "단독"
        if len(cands) > 1:
            how = "순서"
            scored = sorted(
                ((hangul.similarity(slip.merchant, c.vendor), c) for c in cands),
                key=lambda x: -x[0],
            )
            best, runner_up = scored[0], scored[1]
            if best[0] >= VENDOR_MIN and best[0] - runner_up[0] >= VENDOR_MARGIN:
                chosen, how = best[1], "가맹점"
        used.add(chosen.index)
        pairs.append((slip, chosen, how))
    return pairs, skipped


def open_expense(page, slip: Slip, row: Row, report_url: str, folder: Path) -> None:
    """상세 화면을 열고, 이 전표의 금액이 보일 때까지 기다린다.

    주소로 직접 간다. 행을 클릭하면 알림/카드 버튼에 맞아 팝오버만 열리는 일이
    있었다. 행 id가 곧 경비 ID라서 주소를 만들 수 있다.

    대기는 네비게이션이 아니라 '#transactionAmount가 이 금액이 되는 것'으로 한다.
    SPA라 화면 전환에 load 이벤트가 안 나고, 목록과 상세가 한 화면에 같이 보여서
    필드 존재만으로는 맞는 경비를 열었는지 알 수 없다.
    """
    if not row.expense_id:
        raise AttachError("경비 ID를 찾지 못해서 첨부하지 않았습니다.")

    page.goto(expense_url(report_url, row.expense_id), wait_until="domcontentloaded")

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
                print(f"     화면 정보를 저장했습니다: {dump}")
        raise AttachError(
            f"상세 화면에서 {slip.amount:,}원을 확인하지 못했습니다"
            + (f" (화면 금액: {shown})" if shown else " (금액 칸이 없습니다 - 상세가 열리지 않았습니다)")
            + ". 안전을 위해 첨부하지 않았습니다."
        )


def attach(page, slip: Slip, row: Row, report_url: str, folder: Path) -> None:
    open_expense(page, slip, row, report_url, folder)
    page.set_input_files(UPLOAD_INPUT, str(slip.path))
    page.wait_for_timeout(3000)
    page.get_by_role("button", name="경비 저장").first.click()
    page.wait_for_timeout(2000)


def attach_phase(page, report_url: str, folder: Path, apply: bool,
                 tolerance: int, limit: int | None) -> int:
    """열려 있는 리포트에 영수증을 붙인다. 브라우저는 부르는 쪽이 연다."""
    slips = load_manifest(folder)
    done = load_done(folder)
    if done:
        slips = [s for s in slips if s.approval not in done]
        print(f"이미 붙인 {len(done)}건은 건너뜁니다. 남은 전표는 {len(slips)}건입니다.")
    if not slips:
        print("붙일 영수증이 없습니다.")
        return 0

    rows = read_rows(page)
    if not rows:
        dump = folder / "concur-dump.html"
        try:
            dump.write_text(page.content(), encoding="utf-8")
        except PWError:
            dump = None
        raise AttachError(
            "경비 목록을 읽지 못했습니다. 리포트가 열려 있는지 확인해 주세요."
            + (f" 화면 정보를 {dump} 에 남겼습니다." if dump else "")
        )

    dated = [r for r in rows if r.when and r.amount and r.expense_id]
    print(f"\n경비 {len(rows)}건 중 {len(dated)}건을 읽었습니다. 붙일 전표는 {len(slips)}건입니다.")
    if len(dated) != len(rows):
        print(f"  알림: {len(rows) - len(dated)}건은 값을 읽지 못해 대상에서 제외했습니다")

    pairs, skipped = match(slips, dated, tolerance)

    # 이미 영수증이 붙어 있는 경비는 다시 붙이지 않는다. 같은 파일이 두 장
    # 붙으면 감사에서 설명해야 한다. 화면에서 확인할 수 없었던 행(None)은
    # 붙은 것으로 치지 않는다 - 모른다고 건너뛰면 조용히 빠뜨리게 된다.
    already = [(s, r) for s, r, _ in pairs if r.has_receipt]
    if already:
        pairs = [(s, r, h) for s, r, h in pairs if not r.has_receipt]
        print(f"\n이미 영수증이 붙어 있는 {len(already)}건은 건너뜁니다.")
        for slip, _ in already:
            if apply:
                mark_done(folder, slip.approval)

    counts = Counter(how for _, _, how in pairs)
    extra = ", ".join(f"{how} {n}건" for how, n in counts.items() if how != "단독")
    print(f"\n짝을 찾은 것 {len(pairs)}건" + (f" (그중 {extra})" if extra else ""))
    for slip, row, how in pairs:
        gap = (row.when - slip.when).days
        mark = "" if gap == 0 else f"  (Concur 날짜 {gap:+d}일)"
        if how == "가맹점":
            mark += "  [가맹점으로 판별]"
        elif how == "순서":
            mark += "  [순서 배정 - 확인 권장]"
        print(f"  {slip.when} {slip.amount:>9,}원  {slip.merchant[:16]:16} -> {row.text[:50]}{mark}")

    if skipped:
        print(f"\n건너뛴 것 {len(skipped)}건 (직접 처리해 주세요):")
        for slip, why in skipped:
            print(f"  {slip.when} {slip.amount:>9,}원  {slip.merchant[:16]:16} - {why}")

    if not apply:
        print("\n계획만 보여 드렸습니다. 실제로 붙이시려면 --apply 를 붙여 주세요.")
        return 0

    if limit:
        pairs = pairs[:limit]
        print(f"\n--limit {limit} 이라서 {len(pairs)}건만 붙입니다.")

    attached, failed = 0, []
    for i, (slip, row, _) in enumerate(pairs, 1):
        try:
            attach(page, slip, row, report_url, folder)
            mark_done(folder, slip.approval)
            attached += 1
            print(f"  [{i}/{len(pairs)}] 첨부했습니다 - {slip.path.name}")
        except (AttachError, PWTimeout) as exc:
            failed.append((slip, str(exc)))
            print(f"  [{i}/{len(pairs)}] 실패했습니다 - {slip.path.name}: {exc}")

    print(f"\n{attached}건을 첨부했습니다.")
    if failed:
        print(f"{len(failed)}건은 첨부하지 못했습니다:")
        for slip, why in failed:
            print(f"  ! {slip.path.name}: {why}")
        return 1
    return 0


def open_report():
    """브라우저를 열고 사람이 로그인·리포트 열기를 마칠 때까지 기다린다.

    (playwright, context, page, report_url)을 준다. 닫는 것은 부르는 쪽 몫이다.
    C·D단계를 한 세션에서 이어 하려고 분리했다.
    """
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR), headless=False, accept_downloads=True
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(START_URL, wait_until="domcontentloaded")

    print("\n" + "=" * 64)
    print("  Concur에 로그인하시고 처리할 경비 리포트를 열어 주세요.")
    print("  경비 목록이 보이는 상태에서 Enter를 눌러 주세요.")
    print("=" * 64)
    console.wait_enter("리포트를 여셨으면 Enter > ")

    page.wait_for_timeout(2000)  # Enter 직후에도 화면을 더 그린다
    return pw, ctx, page, page.url


def run(folder: Path, apply: bool, tolerance: int, limit: int | None) -> int:
    pw, ctx, page, report_url = open_report()
    try:
        return attach_phase(page, report_url, folder, apply, tolerance, limit)
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="Concur 경비에 전표 첨부")
    ap.add_argument("--dir", type=Path, help="manifest.csv가 있는 폴더입니다. 없으면 설정값을 씁니다")
    ap.add_argument("--apply", action="store_true", help="실제로 첨부합니다")
    ap.add_argument("--tolerance", type=int, help="Concur 날짜 허용 오차(일)입니다. 없으면 설정값을 씁니다")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 처리합니다 (동작 확인용)")
    args = ap.parse_args()
    cfg = settings.load()
    tolerance = args.tolerance if args.tolerance is not None else int(cfg["date_tolerance_days"])
    try:
        return run(args.dir or Path(cfg["downloads_dir"]), args.apply, tolerance, args.limit)
    except AttachError as exc:
        print(f"\n작업을 중단했습니다: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
