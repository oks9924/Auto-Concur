"""현대카드 MY COMPANY에서 매출전표 PDF를 내려받는다.

카드 인증은 사람이 한다. 가상키패드라 값을 넣을 수 없고, 금융 보안장치를
우회할 생각도 하지 않는다. 인증 후의 조회/선택/다운로드 반복만 자동화한다.

    python -m src.download_slips --from 2026.07.01 --to 2026.07.31 --limit 1
    python -m src.download_slips --from 2026.07.01 --to 2026.07.31

처음 돌릴 때는 --limit 1 로 한 건만 받아서 동작을 확인해라.

한 건씩 선택해서 받는다. 전체를 한 번에 선택하면 합본 PDF가 나올 수도 있고
ZIP이 나올 수도 있어서 결과를 예측할 수 없다. 한 건씩이면 항상 파일 하나가
거래 하나에 대응하고, 실패한 건이 어느 것인지도 남는다.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from . import console

SLIP_PAGE = "https://mycompany.hyundaicard.com/hs/cs/HSCS1002.do?_method=s&_proc=authCard"
PROFILE_DIR = Path("browser-profile") / "hyundaicard"

# dhtmlxGrid 객체는 전역 변수에 있는데 이름을 모른다. 이름으로 추측하지 말고
# getAllRowIds를 가진 객체를 찾아서 확인한다. 체크박스 칼럼도 타입('ch')으로 찾는다.
FIND_GRID_JS = """
() => {
  for (const k of Object.keys(window)) {
    let g;
    try { g = window[k]; } catch (e) { continue; }
    if (!g || typeof g !== 'object') continue;
    if (typeof g.getAllRowIds !== 'function' || typeof g.cells !== 'function') continue;
    const cols = g.getColumnsNum ? g.getColumnsNum() : 0;
    let chCol = -1;
    for (let i = 0; i < cols; i++) {
      try { if (g.getColType(i) === 'ch') { chCol = i; break; } } catch (e) {}
    }
    const rows = String(g.getAllRowIds() || '').split(',').filter(Boolean);
    if (rows.length) return { key: k, cols, chCol, rows };
  }
  return null;
}
"""

SET_CHECK_JS = """
(a) => { window[a.key].cells(a.row, a.col).setValue(a.on ? 1 : 0); }
"""

ROW_TEXT_JS = """
(a) => {
  const g = window[a.key];
  const out = [];
  for (let i = 0; i < a.cols; i++) {
    try { out.push(String(g.cells(a.row, i).getValue() ?? '')); } catch (e) { out.push(''); }
  }
  return out;
}
"""


def _norm_date(value: str) -> str:
    """2026-07-01 / 20260701 / 2026.07.01 을 화면 형식(2026.07.01)으로."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise ValueError(f"날짜 형식을 알 수 없다: {value!r}")
    return f"{digits[:4]}.{digits[4:6]}.{digits[6:]}"


def _set_date(page, selector: str, value: str) -> None:
    """달력 위젯이 붙은 입력이라 readonly일 수 있다. 그때는 값을 직접 넣는다."""
    try:
        page.fill(selector, value, timeout=3000)
    except PWTimeout:
        page.eval_on_selector(
            selector,
            "(el, v) => { el.removeAttribute('readonly'); el.value = v;"
            " el.dispatchEvent(new Event('change', {bubbles: true})); }",
            value,
        )


def download(from_date: str, to_date: str, out_dir: Path, limit: int | None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            accept_downloads=True,
            locale="ko-KR",
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(SLIP_PAGE, wait_until="domcontentloaded")

        print("\n브라우저에서 카드 인증을 직접 해라 (가상키패드라 자동 입력이 안 된다).")
        print("매출내역 화면이 뜨면 여기로 돌아와서 Enter.\n")
        input("Enter... ")

        _set_date(page, "#inqFromDt", from_date)
        _set_date(page, "#inqToDt", to_date)
        page.click("#btnIqry")
        page.wait_for_timeout(3000)

        grid = page.evaluate(FIND_GRID_JS)
        if not grid:
            print("그리드 객체를 못 찾았다. 조회가 됐는지 화면을 확인해라.")
            ctx.close()
            return 1
        if grid["chCol"] < 0:
            print(f"체크박스 칼럼을 못 찾았다: {grid}")
            ctx.close()
            return 1

        rows = grid["rows"]
        print(f"그리드 '{grid['key']}': {len(rows)}건, 체크박스 칼럼 {grid['chCol']}")
        if limit:
            rows = rows[:limit]
            print(f"--limit {limit} 이므로 {len(rows)}건만 받는다.")

        # 조회 직후 선택 상태가 남아 있을 수 있으니 전부 해제하고 시작한다.
        for row in grid["rows"]:
            page.evaluate(SET_CHECK_JS, {"key": grid["key"], "row": row, "col": grid["chCol"], "on": False})

        saved, failed = 0, []
        for i, row in enumerate(rows, 1):
            cells = page.evaluate(ROW_TEXT_JS, {"key": grid["key"], "row": row, "cols": grid["cols"]})
            label = " ".join(c for c in cells if c)[:60]

            args = {"key": grid["key"], "row": row, "col": grid["chCol"], "on": True}
            page.evaluate(SET_CHECK_JS, args)
            try:
                with page.expect_download(timeout=60000) as dl:
                    page.evaluate("fnPdf()")
                d = dl.value
                target = out_dir / d.suggested_filename
                d.save_as(target)
                saved += 1
                print(f"  [{i}/{len(rows)}] {target.name}   {label}")
            except PWTimeout:
                failed.append((row, label))
                print(f"  [{i}/{len(rows)}] 실패 - 다운로드가 시작되지 않았다   {label}")
            finally:
                args["on"] = False
                page.evaluate(SET_CHECK_JS, args)

        ctx.close()

    print(f"\n{saved}건 저장 -> {out_dir}")
    if failed:
        print(f"실패 {len(failed)}건:")
        for row, label in failed:
            print(f"  ! row={row}  {label}")
        return 1
    print("다음: python -m src.organize", out_dir)
    return 0


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="현대카드 매출전표 PDF 다운로드")
    ap.add_argument("--from", dest="from_date", required=True, help="예: 2026.07.01")
    ap.add_argument("--to", dest="to_date", required=True, help="예: 2026.07.31")
    ap.add_argument("--out", type=Path, default=Path("downloads"))
    ap.add_argument("--limit", type=int, help="처음엔 1로 테스트")
    args = ap.parse_args()
    return download(_norm_date(args.from_date), _norm_date(args.to_date), args.out, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
