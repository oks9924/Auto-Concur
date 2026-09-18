"""Concur 반영을 한 번에: 카드 경비 입력/영수증 + 저장된 차량 마일리지 신규 생성.

    python -m src.update_concur            # 계획만 (아무것도 안 바꿈)
    python -m src.update_concur --apply    # 반영

따로 돌리면 브라우저를 두 번 띄우고 로그인·리포트 열기를 두 번 해야 한다.
한 세션에서 이어서 한다.

작업지(manifest.xlsx 또는 manifest.csv)가 있으면 그대로 쓴다. 없으면 규칙대로 한다.
"""

from __future__ import annotations
from .concur_formats import configured_run, amount_check_js, room_check_js, range_matches, format_range

import argparse
from pathlib import Path

from . import console, paths, settings, sheet
from .attach_receipts import AttachError, load_manifest, open_report


def pick_sheet(folder: Path, given: str | None) -> Path | None:
    """작업지를 고른다. 사람이 손본 xlsx를 csv보다 먼저 본다."""
    if given:
        return Path(given)
    for name in ("workbook.json", "manifest.xlsx", "manifest.csv"):
        path = folder / name
        if path.exists():
            return path
    return None


def precheck(folder: Path, sheet_path: Path | None, limit: int | None = None) -> None:
    """브라우저를 띄우기 전에 읽을 파일부터 확인한다.

    로그인하고 리포트까지 연 다음에 '작업지가 없다'로 멈추면 그 수고가 헛일이
    된다. 실제로 리포트를 열고 Enter를 누른 직후에 창이 닫히는 일이 있었다.
    """
    slips = load_manifest(folder)
    print(f"전표 {len(slips)}건을 읽었습니다: {folder / 'manifest.csv'}")
    if sheet_path:
        print(f"작업지 {len(sheet.load(sheet_path))}행을 읽었습니다: {sheet_path}")
    from . import mileage_concur
    mileage = mileage_concur.status(folder, limit)
    print(f"마일리지 {mileage['total']}건 · 신규 생성 대상 {mileage['pending']}건 · "
          f"기존 확인 {mileage['verified']}건 · 확인 필요 {mileage['needs_review']}건"
          + (f" · 저장 전 오류 재확인 {mileage.get('reconcile', 0)}건" if mileage.get('reconcile') else ""))


@configured_run
def run(folder: Path, apply: bool, tolerance: int, limit: int | None,
        sheet_path: Path | None, again: bool = False, cfg: dict | None = None) -> int:
    cfg = dict(settings.load() if cfg is None else cfg)
    cfg['date_tolerance_days'] = tolerance
    precheck(folder, sheet_path, limit)
    from . import sequential_workflow, mileage_concur
    from .concur_workflow import RunResult

    mileage = mileage_concur.status(folder, limit)
    pw, ctx, page, report = open_report(automatic=True)
    try:
        message = (
            f'{report.title}\n리포트 ID: {report.key[1]}\n'
            f'현재 리포트 경비 {len(report.rows)}건을 카드 작업지와 비교한 뒤, '
            f'저장된 마일리지 신규 생성 대상 {mileage["pending"]}건을 같은 세션에서 이어 처리합니다.\n'
            f'이미 생성 확인된 마일리지 {mileage["verified"]}건은 건너뛰고, '
            f'이전 확인 필요 {mileage["needs_review"]}건은 자동 재생성하지 않습니다.\n'
            + (f'저장 전 UI 오류 {mileage.get("reconcile", 0)}건은 리포트의 실제 경비 ID를 확인한 뒤 '
               '없을 때만 재시도합니다.\n' if mileage.get('reconcile') else '')
            + '처리 순서: 카드 경비 → 차량 마일리지'
        )
        print(message)
        if apply and not console.confirm_action(message, '카드 경비와 마일리지 함께 반영 시작'):
            return RunResult(0, '사용자가 반영을 취소했습니다. Concur는 변경하지 않았습니다.')

        card = sequential_workflow.run(
            page, report, folder, cfg, sheet_path, apply, limit, again, confirm=False)
        mileage_result = mileage_concur.run_in_session(
            page, report.url, folder, apply, limit)

        summary = f'카드 경비: {getattr(card, "summary", str(card))} / 마일리지: {mileage_result.summary}'
        print('통합 실행 결과: ' + summary)
        result = RunResult(int(bool(int(card) or int(mileage_result))), summary)
        result.card_result = card
        result.mileage_result = mileage_result
        return result
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    cfg = settings.load()
    ap = argparse.ArgumentParser(description="Concur에 첨부와 입력을 한 번에 반영")
    ap.add_argument("--dir", type=Path, help="전표 폴더입니다. 없으면 설정값을 씁니다")
    ap.add_argument("--sheet", help="작업지 경로입니다. 없으면 전표 폴더에서 찾습니다")
    ap.add_argument("--apply", action="store_true", help="실제로 반영합니다")
    ap.add_argument("--limit", type=int, help="각 단계에서 앞 N건만 처리합니다 (동작 확인용)")
    ap.add_argument("--tolerance", type=int, help="영수증 매칭에서 허용할 날짜 오차(일)입니다")
    ap.add_argument("--again", action="store_true",
                    help="영수증이 이미 붙어 있다는 판정을 무시하고 다시 붙입니다")
    args = ap.parse_args()

    folder = args.dir or paths.folder(cfg["downloads_dir"])
    tolerance = args.tolerance if args.tolerance is not None else int(cfg["date_tolerance_days"])
    try:
        return run(folder, args.apply, tolerance, args.limit,
                   pick_sheet(folder, args.sheet), args.again)
    except (AttachError, sheet.SheetError) as exc:
        print(f"\n작업을 중단했습니다: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
