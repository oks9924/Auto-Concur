"""현대카드 MY COMPANY에서 매출전표 PDF를 내려받는다.

카드 인증은 사람이 한다. 가상키패드라 값을 넣을 수 없고, 금융 보안장치를
우회할 생각도 하지 않는다. 인증 후의 조회/선택/다운로드만 자동화한다.

    python -m src.download_slips --from 2026.07.01 --to 2026.07.31 --limit 3
    python -m src.download_slips --from 2026.07.01 --to 2026.07.31

전체를 한 번에 선택해서 합본 PDF 하나로 받고 페이지 단위로 쪼갠다.
'페이지 당 1매씩'을 고르면 한 페이지가 전표 한 장이라 페이지와 거래가
1:1로 대응한다. 한 건씩 받으면 서버가 주는 파일명이 매번 '매출전표_<오늘날짜>'로
같아서 서로 덮어쓴다.

페이지 순서는 신경 쓰지 않는다. 파일 이름은 PDF 내용(거래일시·금액·승인번호)에서
만들기 때문에 그리드 순서와 어긋나도 결과가 틀어지지 않는다.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

from . import browser, console, settings

SLIP_PAGE = "https://mycompany.hyundaicard.com/hs/cs/HSCS1002.do?_method=s&_proc=authCard"
PROFILE_DIR = Path("browser-profile") / "hyundaicard"

# 합본 생성은 건수가 많으면 오래 걸린다.
DOWNLOAD_TIMEOUT_MS = 300_000

# 조회 결과가 그려질 때까지 기다릴 시간.
QUERY_TIMEOUT_MS = 30_000

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

SET_CHECKS_JS = """
(a) => { const g = window[a.key]; a.rows.forEach(r => g.cells(r, a.col).setValue(a.on ? 1 : 0)); }
"""

# fnPdf()는 바로 받지 않고 '출력방식' 모달을 띄운다. 모달 마크업을 모르니
# 화면에 보이는 요소 중 라벨이 정확히 일치하는 가장 안쪽 것을 누른다.
CLICK_TEXT_JS = """
(label) => {
  const hit = [...document.querySelectorAll('a, button, label, span, div, li, p')]
    .filter(e => e.offsetParent !== null && (e.textContent || '').trim() === label);
  if (!hit.length) return false;
  hit.sort((a, b) => a.getElementsByTagName('*').length - b.getElementsByTagName('*').length);
  hit[0].click();
  return true;
}
"""

# 모달 처리에 실패했을 때 다음 수정에 쓸 근거를 남긴다.
DUMP_MODAL_JS = """
() => [...document.querySelectorAll('div')]
  .filter(d => d.offsetParent !== null
            && /출력방식|페이지 당/.test(d.textContent || '')
            && (d.textContent || '').length < 400)
  .map(d => d.outerHTML.slice(0, 4000));
"""

LAYOUT_ONE_PER_PAGE = "페이지 당 1매씩"


class DownloadError(Exception):
    """추측으로 진행하면 안 되는 상태. 멈추고 사람에게 넘긴다."""


def _norm_date(value: str) -> str:
    """2026-07-01 / 20260701 / 2026.07.01 을 화면 형식(2026.07.01)으로."""
    digits = re.sub(r"\D", "", value)
    if len(digits) != 8:
        raise ValueError(f"날짜 형식을 알 수 없습니다: {value!r}")
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


def _click_text(page, label: str) -> bool:
    return bool(page.evaluate(CLICK_TEXT_JS, label))


def _dump_modal(page, out_dir: Path) -> None:
    """모달을 못 다뤘으면 마크업을 남긴다. 추측 대신 근거로 고치기 위해서다."""
    try:
        blocks = page.evaluate(DUMP_MODAL_JS)
    except Exception:
        return
    if blocks:
        path = out_dir / "modal-dump.html"
        path.write_text("\n\n<!-- ======== -->\n\n".join(blocks), encoding="utf-8")
        print(f"  모달 마크업을 저장했습니다: {path}")


def _split(bundle: Path, out_dir: Path, expected: int) -> int:
    """합본 PDF를 한 장씩 쪼갠다. 페이지 수가 건수와 다르면 멈춘다."""
    reader = PdfReader(bundle)
    if len(reader.pages) != expected:
        raise DownloadError(
            f"{expected}건을 선택했는데 받은 PDF는 {len(reader.pages)}페이지입니다. "
            f"페이지와 거래가 1:1이 아니면 이후 매칭을 믿을 수 없어서 여기서 멈춥니다. "
            f"'페이지 당 1매씩'이 아니라 4매씩으로 받았는지 확인해 주세요: {bundle}"
        )
    for i, page in enumerate(reader.pages, 1):
        writer = PdfWriter()
        writer.add_page(page)
        with (out_dir / f"slip_{i:03d}.pdf").open("wb") as f:
            writer.write(f)
    return len(reader.pages)


def download(from_date: str, to_date: str, out_dir: Path, limit: int | None) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "_raw"  # organize의 *.pdf 글롭에 합본이 걸리지 않게 따로 둔다
    raw_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        ctx = browser.launch(p, PROFILE_DIR, accept_downloads=True, locale="ko-KR")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(SLIP_PAGE, wait_until="domcontentloaded")

        print("\n" + "=" * 64)
        print("  브라우저에서 카드 인증을 직접 해 주세요 (가상키패드라 자동 입력이 안 됩니다).")
        print("=" * 64)
        console.wait_enter("매출내역 목록이 뜨는 것까지 확인하셨으면")

        _set_date(page, "#inqFromDt", from_date)
        _set_date(page, "#inqToDt", to_date)
        page.click("#btnIqry")

        # 조회 결과가 올 때까지 기다린다. 3초만 기다렸더니 아직 안 온 화면을
        # 읽어서 '선택 칸이 없다'(chCol: -1, rows: ['1'])로 멈춘 적이 있다.
        # 체크박스 칼럼이 생기는 것이 결과가 다 왔다는 신호다.
        grid = None
        for _ in range(int(QUERY_TIMEOUT_MS / 1000)):
            grid = page.evaluate(FIND_GRID_JS)
            if grid and grid["chCol"] >= 0:
                break
            page.wait_for_timeout(1000)

        if not grid:
            _dump_modal(page, out_dir)
            raise DownloadError("거래 목록을 찾지 못했습니다. 조회가 됐는지 화면을 확인해 주세요.")
        if grid["chCol"] < 0:
            raise DownloadError(
                "조회 결과를 기다렸지만 선택 칸이 생기지 않았습니다. "
                "그 기간에 거래가 없거나 조회가 끝나지 않은 것 같습니다. "
                f"화면을 확인하고 다시 시도해 주세요: {grid}"
            )

        rows = grid["rows"]
        print(f"거래 {len(rows)}건을 찾았습니다.")
        if limit:
            rows = rows[:limit]
            print(f"--limit {limit} 이라서 {len(rows)}건만 받습니다.")

        base = {"key": grid["key"], "col": grid["chCol"]}
        page.evaluate(SET_CHECKS_JS, {**base, "rows": grid["rows"], "on": False})
        page.evaluate(SET_CHECKS_JS, {**base, "rows": rows, "on": True})

        page.evaluate("fnPdf()")
        page.wait_for_timeout(1000)  # 출력방식 모달이 그려질 때까지
        if not _click_text(page, LAYOUT_ONE_PER_PAGE):
            _dump_modal(page, out_dir)
            raise DownloadError(f"출력방식 창에서 '{LAYOUT_ONE_PER_PAGE}'을 찾지 못했습니다.")

        print(f"{len(rows)}건을 한 파일로 만드는 중입니다. 몇 분 걸릴 수 있습니다...")
        try:
            with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl:
                if not _click_text(page, "확인"):
                    raise DownloadError("출력방식 창에서 '확인'을 찾지 못했습니다.")
            downloaded = dl.value
        except PWTimeout:
            _dump_modal(page, out_dir)
            raise DownloadError("다운로드가 시작되지 않았습니다. 건수를 줄여서 다시 시도해 주세요.")

        bundle = raw_dir / downloaded.suggested_filename
        downloaded.save_as(bundle)
        ctx.close()

    print(f"받은 파일을 저장했습니다: {bundle}")
    n = _split(bundle, out_dir, len(rows))
    print(f"전표 {n}장으로 나눴습니다 -> {out_dir}")
    print(f"다음 단계: python -m src.organize {out_dir}")
    console.open_folder(out_dir)
    return 0


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="현대카드 매출전표 PDF 다운로드")
    ap.add_argument("--from", dest="from_date", required=True, help="예: 2026.07.01")
    ap.add_argument("--to", dest="to_date", required=True, help="예: 2026.07.31")
    ap.add_argument("--out", type=Path, help="전표를 받을 폴더입니다. 없으면 설정값을 씁니다")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 받습니다 (동작 확인용)")
    args = ap.parse_args()
    try:
        return download(_norm_date(args.from_date), _norm_date(args.to_date), args.out, args.limit)
    except DownloadError as exc:
        print(f"\n작업을 중단했습니다: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
