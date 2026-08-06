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

from .slip_parser import Slip, SlipParseError, parse_slip

MANIFEST_COLUMNS = [
    "파일명",
    "거래일",
    "거래시각",
    "합계",
    "승인번호",
    "가맹점명",
    "매장명",
    "거래유형",
    "카드번호",
    "사업자등록번호",
    "전표번호",
    "원본파일명",
]


def _row(slip: Slip, filename: str) -> dict[str, str]:
    return {
        "파일명": filename,
        "거래일": slip.transacted_at.strftime("%Y-%m-%d"),
        "거래시각": slip.transacted_at.strftime("%H:%M:%S"),
        "합계": str(slip.total),
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
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        print(f"PDF가 없다: {folder}", file=sys.stderr)
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
            failures.append((pdf, f"이름 충돌: {target.name} 이 이미 있다"))
            continue
        elif apply:
            pdf.rename(target)
            print(f"  → {pdf.name}  ->  {target.name}")
        else:
            print(f"  · {pdf.name}  ->  {target.name}")

        rows.append(_row(slip, target.name))

    rows.sort(key=lambda r: (r["거래일"], r["거래시각"]))

    manifest = folder / "manifest.csv"
    # utf-8-sig: 엑셀에서 한글이 깨지지 않게.
    with manifest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(int(r["합계"]) for r in rows)
    print(f"\n전표 {len(rows)}건, 합계 {total:,}원  ->  {manifest}")
    if not apply:
        print("미리보기다. 실제로 바꾸려면 --apply 를 붙여라.")

    if failures:
        print(f"\n실패 {len(failures)}건 (이름 안 바꿈):", file=sys.stderr)
        for pdf, msg in failures:
            print(f"  ! {pdf.name}: {msg}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="현대카드 전표 PDF 정리")
    ap.add_argument("folder", type=Path, help="전표 PDF가 모여 있는 폴더")
    ap.add_argument("--apply", action="store_true", help="실제로 이름을 변경한다")
    args = ap.parse_args()
    return organize(args.folder, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
