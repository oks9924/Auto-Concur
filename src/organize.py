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
from . import photo_slip
from . import settings, sheet
from .worksheet import NATIVE_NAME, write_json
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


def _edits_from_xlsx(book: Path) -> dict[str, dict[str, str]]:
    """작업지(엑셀)에서 사람이 적은 값을 읽는다.

    data_only=False 로 읽는다. 참석자·숙박위치처럼 우리가 수식으로 넣은 칸은
    수식 그대로 가져와야 다시 만들 때도 수식으로 남는다 - 계산된 값을 가져오면
    수식이 값으로 굳어서, 나중에 유형을 바꿔도 안 따라간다.
    """
    from openpyxl import load_workbook

    ws = load_workbook(book, data_only=False).active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {}
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    if "승인번호" not in header:
        return {}
    kept = {}
    for r in rows[1:]:
        cells = dict(zip(header, r))
        key = str(cells.get("승인번호") or "").strip()
        if key:
            kept[key] = {
                k: ("" if cells.get(k) is None else str(cells[k]).strip())
                for k in EDITABLE
            }
    return kept


def _kept_edits(folder: Path) -> dict[str, dict[str, str]]:
    """사람이 고친 값을 승인번호로 기억해둔다. 엑셀을 먼저 본다.

    다시 돌릴 때마다 미리채움으로 덮어쓰면 손으로 고친 것이 날아간다.

    csv 만 보고 있었다(실측 2026-09-09). 그런데 사람이 여는 것은 엑셀이라,
    엑셀에 적은 값은 아무도 읽지 않았고 B단계를 다시 누를 때마다 통째로
    사라졌다. 숙박 날짜를 적어뒀는데 C단계에서 날짜 칸을 아예 건드리지
    않았던 것이 이것 때문이다.
    """
    native = folder / NATIVE_NAME
    if native.exists():
        return {r['승인번호']: {k: r.get(k, '') for k in EDITABLE}
                for r in sheet.read_raw(native) if r.get('승인번호')}
    book, manifest = folder / "manifest.xlsx", folder / "manifest.csv"
    # 나중에 고친 쪽을 믿는다. 두 파일 모두 B단계가 같이 만들지만, 그 뒤로
    # 사람이 손댄 것은 한쪽뿐이다. 그 한쪽이 더 최근이다.
    xlsx_newer = book.exists() and (
        not manifest.exists() or book.stat().st_mtime >= manifest.stat().st_mtime
    )
    if xlsx_newer:
        try:
            kept = _edits_from_xlsx(book)
            if kept:
                return kept
        except Exception as exc:
            raise sheet.SheetError(f'{book.name}을 읽지 못해 기존 입력을 보존하고 중단합니다: {exc}') from exc

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


def _photo_row(path: Path) -> dict[str, str]:
    """사진 한 장을 manifest 한 줄로. 이름에서 읽은 것만 채운다.

    가맹점명·승인번호 같은 것은 사진에 없다. 비워 둔다 - 지어내면 그게
    근거인 줄 알게 된다. 짝짓기는 날짜와 금액으로 하므로 그 둘이면 된다.
    """
    when, amount, key = photo_slip.parse(path)
    row = dict.fromkeys(MANIFEST_COLUMNS, "")
    row.update({
        "파일명": path.name,
        "거래일": when.isoformat(),
        "거래시각": "",
        "금액": str(amount),
        "승인번호": key,  # 사진에는 승인번호가 없다. 파일 이름이 그 자리를 대신한다
        "원본파일명": path.name,
    })
    return row


def organize(folder: Path, apply: bool) -> int:
    cfg = settings.load()
    kept = _kept_edits(folder)
    pdfs = sorted(folder.glob("*.pdf"))
    photos = sorted(p for p in folder.iterdir() if p.is_file() and photo_slip.is_photo(p))
    if not pdfs and not photos:
        print(f"전표 PDF도 영수증 사진도 없습니다: {folder}", file=sys.stderr)
        return 1

    rows: list[dict[str, str]] = []
    failures: list[tuple[Path, str]] = []

    # 사진은 이름을 바꾸지 않는다. 그 이름이 곧 승인번호 자리이고, 바꾸면 다시
    # 돌릴 때 사람이 적어둔 값과 '이미 붙였다'는 기록을 둘 다 잃는다.
    for photo in photos:
        try:
            row = _photo_row(photo)
        except photo_slip.PhotoNameError as exc:
            failures.append((photo, str(exc)))
            continue
        row.update(kept.get(row["승인번호"]) or dict.fromkeys(EDITABLE, ""))
        rows.append(row)
        print(f"  · {photo.name}  (사진: {row['거래일']} {int(row['금액']):,}원)")

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

    rows.sort(key=lambda r: (r["거래일"], r["거래시각"], r["승인번호"]))

    if not apply or failures:
        print(f'{len(rows)}건을 읽었습니다. ' + ('미리보기이므로 작업 데이터를 저장하지 않았습니다.' if not apply else
                                              '실패한 파일이 있어 기존 입력 데이터를 보존했습니다.'))
        for path, message in failures:
            print(f'  ! {path.name}: {message}', file=sys.stderr)
        return int(bool(failures))

    # 새 프로그램의 저장 원본. 수식은 실행하지 않으며 빈 입력은 그대로 둔다.
    native_rows = [{k: ('' if str(v).startswith('=') else str(v)) for k, v in row.items()} for row in rows]
    if (folder / NATIVE_NAME).exists():
        write_json(folder / 'workbook.previous.json', {'version': 1, 'rows': sheet.read_raw(folder / NATIVE_NAME)})
    write_json(folder / NATIVE_NAME, {'version': 1, 'rows': native_rows})
    manifest = folder / "manifest.csv"
    # utf-8-sig: 엑셀에서 한글이 깨지지 않게.
    with manifest.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(int(r["금액"]) for r in rows)
    photo_count = sum(1 for r in rows if not r["가맹점명"])
    what = f"전표 {len(rows)}건"
    if photo_count:
        what += f" (그중 사진 {photo_count}장)"
    print(f"\n{what}, 합계 {total:,}원을 정리했습니다  ->  {manifest}")
    # 엑셀본은 경비유형 칸에 드롭다운이 걸려 있어 오타로 못 쓰는 값을 막는다.
    print(f"입력 데이터를 만들었습니다: {folder / NATIVE_NAME}")
    print("프로그램의 [작업지 편집]에서 입력해 주세요. 엑셀 파일은 필요하지 않습니다.")
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
