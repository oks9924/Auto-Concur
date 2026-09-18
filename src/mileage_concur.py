"""Create locally prepared vehicle-mileage rows as new Concur expenses.

This is intentionally a separate write path from card-expense matching. Each local row
has its own stable id and is never retried after an uncertain create/save outcome.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from . import attach_receipts as ar, console, paths
from . import concur_ui as ui
from .mileage import MileageBook, checked_row


class MileageConcurError(Exception):
    pass


class UncertainMileageCreate(MileageConcurError):
    """A server write may have happened; automatic retry is unsafe."""


ADD_EXPENSE_JS = """() => {
  const b = document.querySelector('[data-nuiexp="add-expense-menu-button"]');
  if (!b || b.disabled) return null;
  b.setAttribute('data-auto-mileage-add','1');
  return '[data-auto-mileage-add="1"]';
}"""

MILEAGE_TYPE_JS = r"""() => {
  const visible = e => {
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
  };
  const names = ['자동차 마일리지','차량 마일리지','마일리지'];
  const all=[...document.querySelectorAll('[role="menuitem"],[role="option"],button,a')].filter(visible);
  for (const name of names) {
    const hits=all.filter(e => (e.innerText||'').trim()===name);
    if (hits.length===1) {
      hits[0].setAttribute('data-auto-mileage-type','1');
      return '[data-auto-mileage-type="1"]';
    }
  }
  return null;
}"""

FIELD_JS = r"""(names) => {
  const visible = e => {
    if (!e) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
  };
  const norm = s => (s||'').replace(/s+/g,' ').trim().toLowerCase();
  const wants=names.map(norm);
  const controls=[...document.querySelectorAll('input,textarea')].filter(e => !e.disabled && e.type!=='file');
  const byAttr=controls.filter(e => {
    const hay=[e.getAttribute('aria-label'),e.name,e.id,e.placeholder].map(norm).join(' ');
    return wants.some(w => w && hay.includes(w));
  });
  const byLabel=[];
  for (const l of document.querySelectorAll('label')) {
    const t=norm(l.innerText);
    if (!wants.some(w => w && t.includes(w))) continue;
    let e=l.control;
    if (!e && l.htmlFor) e=document.getElementById(l.htmlFor);
    if (!e) e=l.querySelector('input,textarea');
    if (e && !e.disabled && e.type!=='file') byLabel.push(e);
  }
  const hits=[...new Set([...byLabel,...byAttr])].filter(visible);
  if (hits.length!==1) return null;
  document.querySelectorAll('[data-auto-mileage-field]').forEach(e=>e.removeAttribute('data-auto-mileage-field'));
  hits[0].setAttribute('data-auto-mileage-field','1');
  return '[data-auto-mileage-field="1"]';
}"""

COMBO_JS = r"""(names) => {
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
  const norm=s=>(s||'').replace(/s+/g,' ').trim().toLowerCase();
  const wants=names.map(norm);
  const all=[...document.querySelectorAll('[role="combobox"],button')].filter(visible);
  const hits=all.filter(e=>{
    const hay=norm([e.innerText,e.getAttribute('aria-label'),e.getAttribute('data-nuiexp')].join(' '));
    return wants.some(w=>w && hay.includes(w));
  });
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-combo]').forEach(e=>e.removeAttribute('data-auto-mileage-combo'));
  hits[0].setAttribute('data-auto-mileage-combo','1');
  return '[data-auto-mileage-combo="1"]';
}"""

OPTION_JS = r"""(wanted) => {
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
  const hits=[...document.querySelectorAll('[role="option"],li')].filter(e=>visible(e)&&(e.innerText||'').trim()===wanted);
  if(hits.length!==1)return null;
  hits[0].setAttribute('data-auto-mileage-option','1');
  return '[data-auto-mileage-option="1"]';
}"""


def _fill(page, labels, value, what):
    selector = page.evaluate(FIELD_JS, labels)
    if not selector:
        raise MileageConcurError(what + ' 입력칸을 하나로 확인하지 못했습니다.' + ui.diagnose(page, what))
    page.locator(selector).fill(str(value))


def _select_vehicle(page, vehicle):
    selector = page.evaluate(COMBO_JS, ['차량 ID','Vehicle ID','Vehicle'])
    if not selector:
        raise MileageConcurError('차량 ID 선택칸을 확인하지 못했습니다.' + ui.diagnose(page, '마일리지 차량 ID'))
    page.locator(selector).click()
    ui.click_target(page, OPTION_JS, '차량 ID 선택', vehicle)


def _upload_map(page, filename):
    inputs = page.locator('input[type="file"]')
    if inputs.count() == 0:
        buttons = page.locator('[data-nuiexp="rcpt-btn-attach-receipt"]')
        if buttons.count() == 1:
            buttons.click()
            page.wait_for_timeout(300)
            inputs = page.locator('input[type="file"]')
    if inputs.count() != 1:
        raise MileageConcurError('지도 이미지 업로드 입력칸을 하나로 확인하지 못했습니다.' + ui.diagnose(page, '마일리지 지도 첨부'))
    inputs.set_input_files(str(filename))


def _expense_id(url):
    m = re.search(r'/expenses/([^/?#]+)', str(url))
    return m.group(1) if m else None


def create_one(page, report_url, row, map_path):
    """Create exactly one row. Any uncertainty after the first write stops retry."""
    page.goto(report_url, wait_until='domcontentloaded')
    ui.click_target(page, ADD_EXPENSE_JS, '경비 추가')
    page.wait_for_timeout(300)
    ui.click_target(page, MILEAGE_TYPE_JS, '자동차 마일리지 유형')
    page.wait_for_timeout(700)

    # At this point a draft may already exist. Never turn an ambiguity into a retry.
    try:
        _fill(page, ['거래 날짜','Transaction Date','Date'], row['date'], '거래 날짜')
        _fill(page, ['출발지','출발 위치','Origin','From'], row['origin'], '출발지')
        _fill(page, ['도착지','도착 위치','Destination','To'], row['destination'], '도착지')
        _select_vehicle(page, row['vehicle'])
        _fill(page, ['거리','Distance'], row['distance'], '거리')
        _fill(page, ['탑승자 수','Passengers','Passenger Count'], row['passengers'], '탑승자 수')
        _fill(page, ['설명','Description','Business Purpose'], row['description'], '설명')
        _upload_map(page, map_path)
        page.wait_for_timeout(700)
        ui.click_target(page, ui.SAVE_BUTTONS_JS, '마일리지 저장', '저장,Save')
    except Exception as exc:
        raise UncertainMileageCreate(str(exc)) from exc

    page.wait_for_timeout(1000)
    ident = _expense_id(page.url)
    if not ident:
        raise UncertainMileageCreate('저장 후 새 경비 ID를 확인하지 못했습니다.' + ui.diagnose(page, '마일리지 저장 후 확인'))
    return ident


def run(folder: Path, apply=True, limit=None):
    book = MileageBook(folder)
    if book.dirty:
        raise MileageConcurError('마일리지 표에 저장하지 않은 변경이 있습니다. 먼저 저장하세요.')
    pending = [r for r in book.rows if r.get('concur_state') not in ('verified','needs_review')]
    blocked = [r for r in book.rows if r.get('concur_state') == 'needs_review']
    if blocked:
        print(f'이전 실행에서 확인이 필요한 마일리지 {len(blocked)}건은 자동 재시도하지 않습니다.')
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        print('새로 생성할 마일리지가 없습니다.')
        return 0

    if not apply:
        print(f'미리보기: Concur 신규 마일리지 {len(pending)}건')
        return 0

    pw, ctx, page, report_url = ar.open_report()
    try:
        for n, raw in enumerate(pending, 1):
            row = checked_row(raw)
            image = book.check_image(row)
            print(f'[{n}/{len(pending)}] 마일리지 신규 생성: {row["date"]} · {row["origin"]} → {row["destination"]} · {row["distance"]}km')
            try:
                ident = create_one(page, report_url, row, image)
            except UncertainMileageCreate as exc:
                raw['concur_state'] = 'needs_review'
                raw['concur_note'] = str(exc)
                book.rows = book.validate_rows(book.rows)
                book.save()
                print('  확인 필요: 서버에 초안/저장이 생겼을 수 있어 자동 재시도하지 않습니다.')
                print('  ' + str(exc))
                return 1
            raw['concur_state'] = 'verified'
            raw['concur_expense_id'] = ident
            raw['concur_note'] = '저장 후 경비 ID 확인'
            book.rows = book.validate_rows(book.rows)
            book.save()
            print(f'  확인 완료: {ident}')
        return 0
    finally:
        ctx.close()
        pw.stop()


def main():
    console.setup()
    ap=argparse.ArgumentParser(description='로컬 마일리지 표를 Concur 신규 경비로 생성')
    ap.add_argument('--dir',type=Path)
    ap.add_argument('--limit',type=int)
    ap.add_argument('--preview',action='store_true')
    args=ap.parse_args()
    folder=args.dir or paths.folder('')
    try:
        return run(folder, not args.preview, args.limit)
    except (MileageConcurError, ValueError, OSError) as exc:
        print('\n작업을 중단했습니다: ' + str(exc))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
