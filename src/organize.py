"""전표 PDF 폴더를 정리한다: 이름 변경 + manifest.csv 생성.

manifest.csv가 다음 단계(Concur 첨부/수정)로 넘기는 유일한 인계물이다.
여기서 한 번 눈으로 확인하고 넘어가라는 뜻이기도 하다.

    python -m src.organize ./downloads            # 미리보기 (아무것도 안 바꿈)
    python -m src.organize ./downloads --apply    # 실제 이름 변경
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from . import console
from . import settings, sheet
from .slip_parser import Slip, SlipParseError, parse_slip

# 앞쪽은 전표에서 읽은 사실, 뒤쪽 EDITABLE은 사람이 고쳐서 Concur에 넣을 값이다.
# 숙박비 칸(입실·퇴실·숙박위치·Booking Channel)은 sheet가 정의를 갖고 있다.
EDITABLE = list(sheet.EDITABLE)

# 미리 채우지 않는다. Concur에 들어갈 값은 사람이 엑셀에서 정한다.
# 참석자·숙박위치·Booking Channel만 수식으로 넣어서, 그 경비유형을 고른 행에만
# 나타나게 한다. 사람이 손댈 칸은 '추가 참석자' 쪽이다.

# 엑셀에서 감출 칼럼. 지우지는 않는다 - 가맹점명은 후보가 여럿일 때 어느 경비인지
# 가리는 데 쓰고, 파일명은 첨부할 PDF를 찾는 데, 승인번호는 다시 돌릴 때 사람이
# 적어둔 값을 되찾는 열쇠로 쓴다. 나머지도 나중에 근거를 되짚을 때 필요하다.
HIDDEN = [
    "파일명",
    "승인번호",
    "가맹점명",
    "거래유형",
    "카드번호",
    "사업자등록번호",
    "전표번호",
    "원본파일명",
]

MANIFEST_COLUMNS = [
    "파일명",
    "거래일",
    "거래시각",
    "금액",
    "승인번호",
    "가맹점명",
    "매장명",
    "거래유형",
    "카드번호",
    "사업자등록번호",
    "전표번호",
    "원본파일명",
    *EDITABLE,
]


def _kept_edits(manifest: Path) -> dict[str, dict[str, str]]:
    """이미 있는 manifest에서 사람이 고친 값을 승인번호로 기억해둔다.

    다시 돌릴 때마다 미리채움으로 덮어쓰면 손으로 고친 것이 날아간다.
    """
    if not manifest.exists():
        return {}
    with manifest.open(encoding="utf-8-sig", newline="") as f:
        return {
            r["승인번호"]: {k: r.get(k, "") for k in EDITABLE}
            for r in csv.DictReader(f)
            if r.get("승인번호")
        }


def _row(slip: Slip, filename: str) -> dict[str, str]:
    return {
        "파일명": filename,
        "거래일": slip.transacted_at.strftime("%Y-%m-%d"),
        "거래시각": slip.transacted_at.strftime("%H:%M:%S"),
        "금액": str(slip.total),
        "승인번호": slip.approval_no,
        "가맹점명": slip.merchant_name,
        "매장명": slip.store_name,
        "거래유형": slip.tx_type,
        "카드번호": slip.card_no,
        "사업자등록번호": slip.merchant_biz_no,
        "전표번호": slip.slip_no,
        "원본파일명": Path(slip.source).name,
    }


def organize(folder: Path, apply: bool) -> int:
    cfg = settings.load()
    kept = _kept_edits(folder / "manifest.csv")
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"PDF가 없습니다: {folder}", file=sys.stderr)
        return 1

    rows: list[dict[str, str]] = []
    failures: list[tuple[Path, str]] = []

    for pdf in pdfs:
        try:
            slip = parse_slip(pdf)
        except Exception as exc:  # 파싱 실패든 손상된 PDF든 여기서 멈추고 기록만 한다
            failures.append((pdf, str(exc)))
            continue

        target = folder / slip.filename()
        if target == pdf:
            print(f"  = {pdf.name}  (이미 정리됨)")
        elif target.exists():
            # 승인번호가 고유키라 이름이 겹치면 같은 거래를 두 번 받은 것이다.
            # 덮어쓰지 않고 남겨서 사람이 확인하고 지우게 한다.
            failures.append(
                (pdf, f"{target.name} 이 이미 있습니다 - 같은 거래를 두 번 받으신 것 같습니다. 이 파일은 지우셔도 됩니다")
            )
            continue
        elif apply:
            pdf.rename(target)
            print(f"  → {pdf.name}  ->  {target.name}")
        else:
            print(f"  · {pdf.name}  ->  {target.name}")

        row = _row(slip, target.name)
        # 사람이 엑셀에 적어둔 값이 있으면 그것이 우선이다. 없으면 빈 칸이다.
        row.update(kept.get(slip.approval_no) or dict.fromkeys(EDITABLE, ""))
        rows.append(row)

    rows.sort(key=lambda r: (r["거래일"], r["거래시각"]))

    manifest = folder / "manifest.csv"
    # utf-8-sig: 엑셀에서 한글이 깨지지 않게.
    with manifest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(int(r["금액"]) for r in rows)
    print(f"\n전표 {len(rows)}건, 합계 {total:,}원을 정리했습니다  ->  {manifest}")
    # 엑셀본은 경비유형 칸에 드롭다운이 걸려 있어 오타로 못 쓰는 값을 막는다.
    book = folder / "manifest.xlsx"
    try:
        sheet.write_xlsx(MANIFEST_COLUMNS, rows, book, settings.choices(cfg),
                         hidden=HIDDEN, type_defaults=settings.type_defaults(cfg))
        print(f"작업지를 만들었습니다: {book}  (경비유형은 드롭다운에서만 고르실 수 있습니다)")
    except sheet.SheetError as exc:
        print(f"xlsx는 만들지 못했습니다: {exc}")
    print("엑셀에서 경비유형을 고르신 뒤 초록으로 바뀌는 칸을 채워 주세요.")
    print("  내부 직원간 식음료 -> 같이 드신 분이 있으면 '추가 참석자' 에 적어 주세요")
    print("  숙박비 -> 입실·퇴실 날짜, 숙박위치, Booking Channel")
    print("그 다음 update_concur 로 넘겨 주세요.")
    if not apply:
        print("미리보기입니다. 실제로 바꾸시려면 --apply 를 붙여 주세요.")

    console.open_folder(folder)

    if failures:
        print(f"\n{len(failures)}건은 처리하지 못했습니다 (이름을 바꾸지 않았습니다):", file=sys.stderr)
        for pdf, msg in failures:
            print(f"  ! {pdf.name}: {msg}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="현대카드 전표 PDF 정리")
    ap.add_argument("folder", type=Path, help="전표 PDF가 모여 있는 폴더입니다")
    ap.add_argument("--apply", action="store_true", help="실제로 이름을 바꿉니다")
    args = ap.parse_args()
    return organize(args.folder, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
