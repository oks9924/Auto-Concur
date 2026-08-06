"""Concur 반영을 한 번에: 영수증 첨부 + 유형·목적·코멘트·참석자.

    python -m src.update_concur            # 계획만 (아무것도 안 바꿈)
    python -m src.update_concur --apply    # 반영

따로 돌리면 브라우저를 두 번 띄우고 로그인·리포트 열기를 두 번 해야 한다.
한 세션에서 이어서 한다.

작업지(manifest.xlsx 또는 manifest.csv)가 있으면 그대로 쓴다. 없으면 규칙대로 한다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import console, settings, sheet
from .attach_receipts import AttachError, attach_phase, open_report
from .fix_expenses import fix_phase


def pick_sheet(folder: Path, given: str | None) -> Path | None:
    """작업지를 고른다. 사람이 손본 xlsx를 csv보다 먼저 본다."""
    if given:
        return Path(given)
    for name in ("manifest.xlsx", "manifest.csv"):
        path = folder / name
        if path.exists():
            return path
    return None


def run(folder: Path, apply: bool, tolerance: int, limit: int | None,
        sheet_path: Path | None) -> int:
    cfg = settings.load()
    pw, ctx, page, report_url = open_report()
    try:
        print("\n" + "-" * 64)
        print("[1/2] 영수증 첨부")
        print("-" * 64)
        first = attach_phase(page, report_url, folder, apply, tolerance, limit)

        # 첨부하면서 상세 화면을 여러 번 오갔다. 목록으로 돌아가 다시 읽게 한다.
        page.goto(report_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        print("\n" + "-" * 64)
        print("[2/2] 경비유형 · 목적 · 코멘트 · 참석자")
        if sheet_path:
            print(f"      작업지: {sheet_path}")
        else:
            print("      작업지가 없어서 규칙대로 한다.")
        print("-" * 64)
        second = fix_phase(page, report_url, cfg, apply, limit, sheet_path)
        return first or second
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    cfg = settings.load()
    ap = argparse.ArgumentParser(description="Concur에 첨부와 입력을 한 번에 반영")
    ap.add_argument("--dir", type=Path, help="전표 폴더. 없으면 설정값")
    ap.add_argument("--sheet", help="작업지 경로. 없으면 전표 폴더에서 찾는다")
    ap.add_argument("--apply", action="store_true", help="실제로 반영한다")
    ap.add_argument("--limit", type=int, help="각 단계에서 앞 N건만 (동작 확인용)")
    ap.add_argument("--tolerance", type=int, help="영수증 매칭 날짜 허용 오차(일)")
    args = ap.parse_args()

    folder = args.dir or Path(cfg["downloads_dir"])
    tolerance = args.tolerance if args.tolerance is not None else int(cfg["date_tolerance_days"])
    try:
        return run(folder, args.apply, tolerance, args.limit, pick_sheet(folder, args.sheet))
    except (AttachError, sheet.SheetError) as exc:
        print(f"\n중단: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
