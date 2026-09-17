"""Concur 경비에 전표 PDF를 첨부한다.

로그인(SSO)과 리포트 열기는 사람이 한다. 스크립트는 열려 있는 리포트의
경비 목록을 읽어서 manifest.csv와 맞춰보고, 확실한 것만 첨부한다.

    python -m src.attach_receipts                 # 매칭 계획만 출력 (아무것도 안 바꿈)
    python -m src.attach_receipts --apply         # 실제로 첨부

기본은 계획 출력이다. 잘못 붙인 전표는 감사에서 문제가 되므로 사람이 먼저 본다.

매칭 규칙: 금액과 허용 범위 내 거래일로 찾고, 가맹점 구별과 1:1 관계까지
확인한다. 불완전하거나 구별되지 않는 관련 후보가 있으면 해당 거래만 보류한다. 카드 매입 처리 때문에
Concur 날짜가 거래일과 하루 어긋날 수 있어서 ±1일을 둔다.
"""

from __future__ import annotations
from .concur_formats import configured_run, amount_check_js, room_check_js, range_matches, format_range

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import browser, console, hangul, paths, settings, concur_ui
from .concur_ui import ConcurUIError as AttachError
from .target_matching import VENDOR_MIN, VENDOR_MARGIN
from .concur_values import resolve_dates, parse_amount, amount_from_summary
from .concur_rows import rows_complete, rows_observed, readiness_detail, snapshot_signature

PROFILE_DIR = paths.at("browser-profile", "concur")
START_URL = "https://travel.siemens.cloud"

UPLOAD_INPUT = "#upload-file"
AMOUNT_FIELD = "#transactionAmount"

# 행의 id가 곧 경비 ID다. 상세 주소가 .../reports/{리포트}/expenses/{경비} 라서
# 주소를 직접 만들 수 있다. 클릭해서 여는 것보다 훨씬 확실하다.
#
# data-testid="data-row" 로 데이터 행만 고른다. role=row 만 보면 헤더와 합계
# 행까지 섞여서 인덱스가 어긋난다(실제로 합계 행을 눌렀었다).
# 값은 data-nuiexp 훅에서 직접 읽는다. 칼럼 순서를 짐작하지 않아도 된다.
ROWS_FN = """
  const gridRows = () => [...document.querySelectorAll('[role="row"][data-testid="data-row"]')];
"""

from .concur_rows import READ_ROWS_JS

# 행을 읽지 못했을 때 남길 근거. 훅 이름이 바뀌었는지, 칸이 숨겨졌는지는
# 이 마크업을 봐야 안다. 추측으로 셀렉터를 고치지 않는다.
DUMP_ROWS_JS = (
    "() => {"
    + ROWS_FN
    + """
  // 마크업을 통째로 남기면 잘려서 정작 필요한 칸이 안 보인다. 칸 목록만
  // 추린다 - 어떤 훅이 있고 거기에 뭐가 적혀 있는지가 우리가 볼 전부다.
  return gridRows().slice(0, 3).map((r, i) => ({
    row: i,
    id: r.id || null,
    cells: [...r.querySelectorAll('[role="cell"]')].map(c => ({
      col: c.getAttribute('aria-colindex'),
      hook: c.getAttribute('data-nuiexp') || c.getAttribute('data-testid') || null,
      text: (c.innerText || c.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
    })),
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

# 영수증 판정의 근거를 남긴다. '전부 붙어 있다'가 정말인지, 아니면 칸을 보고
# 붙었다고 잘못 읽은 것인지는 이 마크업을 봐야 안다.
DUMP_RECEIPTS_JS = (
    "() => {"
    + ROWS_FN
    + """
  return gridRows().slice(0, 4).map((r, i) => {
    const cell = r.querySelector('[data-nuiexp="receipts-cell"], [class*="receipt-cell"]');
    const hit = cell && cell.querySelector('[data-nuiexp^="receipt-thumbnail-button"]');
    return {
      row: i,
      cellFound: !!cell,
      thumbnail: hit ? hit.getAttribute('data-nuiexp') : null,
      inner: cell ? cell.outerHTML.slice(0, 1500) : null,
    };
  });
}"""
)

# 상세 폼이 이 전표의 금액을 보여줄 때까지 기다린다. 대기와 검증을 한 번에 한다.
# SPA라 행을 눌러도 load 이벤트가 안 나므로 네비게이션을 기다리면 안 된다.
WAIT_AMOUNT_JS = amount_check_js()

def expense_url(report_url: str, expense_id: str) -> str:
    """리포트 주소에서 경비 상세 주소를 만든다."""
    base = report_url.split("/expenses/")[0].split("?")[0].rstrip("/")
    return f"{base}/expenses/{expense_id}"

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
AMOUNT_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


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
    # 화면에 실제로 적혀 있던 글자. 날짜·금액을 못 읽었을 때 이걸 보여줘야
    # '비어 있었는지' 와 '형식이 달랐는지' 를 가릴 수 있다. 화면 말이 영어면
    # 날짜가 08/09/2026 로 나오는데, 우리는 2026-08-09 만 읽는다.
    raw_date: str = ""
    raw_amount: str = ""
    receipt_file: str = ""  # 붙어 있는 영수증 파일 이름 (화면이 알려준다)
    read_problem: str = ""


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


# 화면낭독기용 요약에서 금액을 꺼낸다. 'KRW 17,000' 처럼 통화 코드 뒤에 붙는다.
# KRW 사이의 공백은 &nbsp;(\xa0)라서 \s로 잡는다.
LABEL_AMOUNT_RE = re.compile(r"(?:[A-Z]{3}|₩|\$)\s*([\d,]+(?:\.\d+)?)")


def amount_from_label(label: str) -> int | None:
    return amount_from_summary(label)


def _parse_amount(text: str) -> int | None:
    return parse_amount(text)


def print_unreadable(rows: list[Row]) -> None:
    """못 읽은 행에 실제로 적혀 있던 글자를 보여준다.

    '읽지 못했습니다'만 있으면 비어 있었는지 형식이 달랐는지 알 수 없다.
    화면 말이 영어면 날짜가 '08/09/2026' 으로 나오는데 우리는 '2026-08-09'
    만 읽는다 - 그런 것은 이 줄을 봐야 드러난다.
    """
    bad = [r for r in rows if r.when is None or r.amount is None]
    if not bad:
        return
    print("  화면에 적혀 있던 글자:")
    for r in bad[:5]:
        print(f"    [{r.index + 1}] 날짜 {r.raw_date!r}  금액 {r.raw_amount!r}")


def dump_rows(page) -> str | None:
    """행 마크업을 파일로 남긴다. 값을 못 읽었을 때 근거가 된다.

    훅 이름이 바뀐 것인지, 칸이 숨겨져 글자가 안 나오는 것인지는 이것을 봐야
    안다. 이 저장소의 규칙이다 - 셀렉터는 추측하지 않고 실물을 보고 고친다.
    """
    try:
        cells = _eval(page, DUMP_ROWS_JS)
    except Exception:
        return None
    out = paths.at("inspect-out")
    out.mkdir(exist_ok=True)
    path = out / "concur-rows.json"
    path.write_text(json.dumps(cells, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def read_rows(page) -> list[Row]:
    raw_rows = _eval(page, READ_ROWS_JS)
    dates = resolve_dates(raw_rows)
    rows = []
    for raw, when in zip(raw_rows, dates):
        amount = _parse_amount(raw.get("amount", ""))
        backup = amount_from_label(raw.get("label", ""))
        if amount is None and not str(raw.get("amount", "")).strip():
            amount = backup
        elif amount is not None and backup is not None and amount != backup:
            amount = None
        label = " ".join(x for x in (raw.get("expenseType", ""), raw.get("vendor", ""), raw.get("amount", "")) if x)
        rows.append(Row(index=raw["index"], when=when, amount=amount,
                        text=label[:80], expense_id=raw.get("id"),
                        expense_type=raw.get("expenseType", ""), vendor=raw.get("vendor", ""),
                        has_receipt=raw.get("receipt"), raw_date=raw.get("date", ""),
                        raw_amount=raw.get("amount", "") or raw.get("label", ""),
                        receipt_file=raw.get("receiptFile", ""), read_problem=raw.get("readProblem", "")))
    return rows


def rows_when_ready(page, tries: int = 90, wait_ms: int = 500,
                    allow_incomplete: bool = False) -> list[Row]:
    """목록이 안정될 때까지 기다린다. 대상별 처리에서는 불완전 행도 보존한다."""
    previous, repeats = None, 0
    for _ in range(tries):
        rows = read_rows(page)
        complete = rows_complete(rows)
        observed = rows_observed(rows) if allow_incomplete else complete
        signature = snapshot_signature(rows)
        repeats = repeats + 1 if observed and signature == previous else 0
        # 늦게 채워지는 필드와 영구적인 미확인을 혼동하지 않도록 불완전 행은 더 기다린다.
        if observed and repeats >= (3 if not complete else 1):
            return rows
        previous = signature if observed else None
        if _ in (10, 30, 60):
            print("  목록 확인 중: " + readiness_detail(rows))
        page.wait_for_timeout(wait_ms)
    raise AttachError("경비 목록을 안전하게 읽지 못했습니다. 일부 행만으로 매칭하지 않았습니다.\n"
                      + readiness_detail(rows) + concur_ui.diagnose(page, "경비 목록 준비"))


def match(slips: list[Slip], rows: list[Row], tolerance_days: int) -> tuple[list, list]:
    """대상별 매칭. 불완전 후보 보존, 중복/애매한 거래 보류, 순서 배정 금지."""
    from .target_matching import match as safe_match
    return safe_match(slips, rows, tolerance_days)


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
        page.wait_for_function(amount_check_js(), arg=str(slip.amount), timeout=20000)
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
    concur_ui.click_target(page, concur_ui.SAVE_BUTTONS_JS, '경비 저장',
                          '경비 저장,Save Expense', timeout=30000)
    page.wait_for_timeout(2000)


def attach_phase(page, report_url: str, folder: Path, apply: bool,
                 tolerance: int, limit: int | None, again: bool = False) -> int:
    """열려 있는 리포트에 영수증을 붙인다. 브라우저는 부르는 쪽이 연다."""
    slips = load_manifest(folder)
    done = load_done(folder)
    if done:
        # 완료 전표를 빼고 매칭하면 동액 거래의 연결이 밀린다. 전체를 비교한다.
        print(f"{done_path(folder).name} 에 {len(done)}건의 기록이 있습니다. "
              "기록만으로 제외하지 않고 현재 화면의 영수증을 확인합니다.")
    if not slips:
        print("붙일 영수증이 없습니다.")
        if done:
            print(f"  (화면에는 영수증이 없는데 여기서 멈췄다면 {done_path(folder)} 때문입니다."
                  "\n   그 파일을 지우거나 --again 으로 돌리면 처음부터 다시 붙입니다.)")
        return 0

    rows = rows_when_ready(page, allow_incomplete=True)
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

    dated = [r for r in rows if r.when is not None and r.amount is not None and r.expense_id]
    print(f"\n경비 {len(rows)}건 중 {len(dated)}건을 읽었습니다. 붙일 전표는 {len(slips)}건입니다.")
    if len(dated) != len(rows):
        print(f"  알림: {len(rows) - len(dated)}건은 값을 읽지 못해 관련 거래만 보류합니다")
        print_unreadable(rows)
        dump = dump_rows(page)
        if dump:
            print(f"  (행 마크업을 {dump} 에 남겼습니다)")

    pairs, skipped = match(slips, rows, tolerance)

    # 이미 영수증이 붙어 있는 경비는 다시 붙이지 않는다. 같은 파일이 두 장
    # 붙으면 감사에서 설명해야 한다. 화면에서 확인할 수 없었던 행(None)은
    # 붙은 것으로 치지 않는다 - 모른다고 건너뛰면 조용히 빠뜨리게 된다.
    already = [] if again else [(s, r) for s, r, _ in pairs if r.has_receipt]
    if already:
        pairs = [(s, r, h) for s, r, h in pairs if not r.has_receipt]
        yes = sum(1 for r in dated if r.has_receipt)
        no = sum(1 for r in dated if r.has_receipt is False)
        unknown = len(dated) - yes - no
        print(f"\n이미 영수증이 붙어 있는 {len(already)}건은 건너뜁니다.")
        # 무엇이 붙어 있어서 건너뛰는지 적는다. 파일 이름이 다르면 엉뚱한
        # 영수증이 붙어 있다는 뜻이라 사람이 봐야 한다.
        for slip, row in already:
            print(f"    {row.when} {(row.amount or 0):>9,}원  화면: "
                  f"{row.receipt_file or '(파일 이름 없음)'}  작업지: {slip.path.name}")
        print(f"  화면 판정: 붙음 {yes} / 안 붙음 {no} / 알 수 없음 {unknown}")
        if no == 0:
            # 전부 '붙음'이면 정말 다 붙은 것일 수도, 영수증 칸을 보고 잘못 읽은
            # 것일 수도 있다. 둘을 가르려면 마크업을 봐야 한다.
            dump = folder / "concur-receipts.json"
            try:
                dump.write_text(
                    json.dumps(_eval(page, DUMP_RECEIPTS_JS), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"  전부 '붙음'으로 나왔습니다. 판정 근거를 남겼습니다: {dump}")
            except Exception:
                pass
            print("  화면에 영수증 아이콘이 정말 다 있는지 한 번 봐 주세요.")
            print("  아니라면 --again 을 붙여서 이 판정을 무시하고 붙일 수 있습니다.")
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
    return int(bool(skipped))


def open_report(automatic=False):
    """브라우저를 열고 사람이 로그인·리포트 열기를 마칠 때까지 기다린다.

    (playwright, context, page, report_url)을 준다. 닫는 것은 부르는 쪽 몫이다.
    C·D단계를 한 세션에서 이어 하려고 분리했다.
    """
    pw = sync_playwright().start()
    ctx = browser.launch(pw, PROFILE_DIR, accept_downloads=True, locale='ko-KR')
    page = browser.open_first(ctx, START_URL)

    if automatic:
        from .report_session import ready_report
        try:
            report = ready_report(page)
            return pw, ctx, page, report
        except Exception:
            ctx.close()
            pw.stop()
            raise

    print("\n" + "=" * 64)
    print("  Concur에 로그인하시고 처리할 경비 리포트를 열어 주세요.")
    print("=" * 64)
    console.wait_enter("경비 목록이 보이면")

    page.wait_for_timeout(2000)  # Enter 직후에도 화면을 더 그린다
    return pw, ctx, page, page.url


@configured_run
def run(folder: Path, apply: bool, tolerance: int, limit: int | None, again: bool) -> int:
    pw, ctx, page, report_url = open_report()
    try:
        return attach_phase(page, report_url, folder, apply, tolerance, limit, again)
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
    ap.add_argument("--again", action="store_true",
                    help="이미 붙어 있다는 판정을 무시하고 다시 붙입니다")
    args = ap.parse_args()
    cfg = settings.load()
    tolerance = args.tolerance if args.tolerance is not None else int(cfg["date_tolerance_days"])
    try:
        return run(args.dir or paths.folder(cfg["downloads_dir"]), args.apply, tolerance,
                   args.limit, args.again)
    except AttachError as exc:
        print(f"\n작업을 중단했습니다: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
