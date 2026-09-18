"""Create locally prepared vehicle-mileage rows as new Concur expenses.

Mileage is executed after card expenses in the same report/browser session. A local
row has a stable id. Verified rows and uncertain writes are never automatically
created again.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from playwright.sync_api import Error as PWError

from . import attach_receipts as ar, console, paths
from . import concur_ui as ui
from .concur_workflow import RunResult
from .mileage import MileageBook, checked_row
from .report_session import check_context


class MileageConcurError(Exception):
    pass


class UncertainMileageCreate(MileageConcurError):
    """A server write may have happened; automatic retry is unsafe."""
    def __init__(self, message, expense_id=None):
        super().__init__(message)
        self.expense_id = expense_id


ADD_EXPENSE_JS = """() => {
  const b = document.querySelector('[data-nuiexp="add-expense-menu-button"]');
  if (!b || b.disabled) return null;
  b.setAttribute('data-auto-mileage-add','1');
  return '[data-auto-mileage-add="1"]';
}"""

SURFACE_HELPERS = r"""
  const visible = e => {
    if (!e) return false;
    const r=e.getBoundingClientRect(), s=getComputedStyle(e);
    return r.width>0 && r.height>0 && s.display!=='none'
      && s.visibility!=='hidden' && s.visibility!=='collapse';
  };
  const surface = () => {
    const nodes=[...document.querySelectorAll(
      '[role="dialog"],[role="alertdialog"],.sapcnqr-dialog__body,[class*="side-panel__side"]'
    )].filter(visible);
    if (!nodes.length) return document;
    return nodes.sort((a,b) => {
      const za=parseInt(getComputedStyle(a).zIndex)||0;
      const zb=parseInt(getComputedStyle(b).zIndex)||0;
      return za-zb;
    }).pop();
  };
  const norm = s => (s||'').replace(/\s+/g,' ').trim();
"""

EXACT_TEXT_JS = "(names) => {" + SURFACE_HELPERS + r"""
  const root=surface();
  const all=[...root.querySelectorAll('*')].filter(visible);
  for (const name of names) {
    const exact=all.filter(e => norm(e.innerText)===name);
    const labels=exact.filter(e => ![...e.children].some(c => visible(c) && norm(c.innerText)===name));
    const targets=[...new Set(labels.map(e => {
      const clickable=e.closest('button,[role="button"],[role="option"],[role="menuitem"],a,[class*="expense-type-list__expense-type-button"]');
      return clickable && root.contains(clickable) ? clickable : e;
    }))];
    if (targets.length===1) {
      document.querySelectorAll('[data-auto-mileage-text]')
        .forEach(e=>e.removeAttribute('data-auto-mileage-text'));
      targets[0].setAttribute('data-auto-mileage-text','1');
      return '[data-auto-mileage-text="1"]';
    }
  }
  return null;
}"""

MILEAGE_TYPE_JS = "() => {" + SURFACE_HELPERS + r"""
  const root=surface();
  const names=['자동차 마일리지','차량 마일리지','마일리지'];
  const all=[...root.querySelectorAll('*')].filter(visible);
  for (const name of names) {
    const exact=all.filter(e => norm(e.innerText)===name);
    const labels=exact.filter(e => ![...e.children].some(c => visible(c) && norm(c.innerText)===name));
    const targets=[...new Set(labels.map(e => {
      const clickable=e.closest('button,[role="button"],[role="option"],[role="menuitem"],a,[class*="expense-type-list__expense-type-button"]');
      return clickable && root.contains(clickable) ? clickable : e;
    }))];
    if (targets.length) {
      document.querySelectorAll('[data-auto-mileage-type]')
        .forEach(e=>e.removeAttribute('data-auto-mileage-type'));
      targets[0].setAttribute('data-auto-mileage-type','1');
      return '[data-auto-mileage-type="1"]';
    }
  }
  return null;
}"""

MILEAGE_FORM_READY_JS = "() => {" + SURFACE_HELPERS + r"""
  const root=surface(), text=norm(root.innerText).toLowerCase();
  const labels=['거래 날짜','출발지','도착지','거리','transaction date','origin','destination','distance'];
  const hits=labels.filter(x=>text.includes(x.toLowerCase())).length;
  const controls=[...root.querySelectorAll('input,textarea,[role="combobox"]')].filter(visible);
  return hits>=2 && controls.length>=2;
}"""

FIELD_JS = "(names) => {" + SURFACE_HELPERS + r"""
  const root=surface(), wants=names.map(x=>norm(x).toLowerCase());
  const controls=[...root.querySelectorAll('input,textarea,[contenteditable="true"]')]
    .filter(e => visible(e) && !e.disabled && e.type!=='file');
  const byAttr=controls.filter(e => {
    const hay=[e.getAttribute('aria-label'),e.name,e.id,e.placeholder]
      .map(x=>norm(x).toLowerCase()).join(' ');
    return wants.some(w => w && hay.includes(w));
  });
  const byLabel=[];
  for (const l of root.querySelectorAll('label')) {
    const t=norm(l.innerText).toLowerCase();
    if (!wants.some(w => w && (t===w || t.includes(w)))) continue;
    let e=l.control;
    if (!e && l.htmlFor) e=document.getElementById(l.htmlFor);
    if (!e) e=l.querySelector('input,textarea,[contenteditable="true"]');
    if (e && root.contains(e) && visible(e) && !e.disabled && e.type!=='file') byLabel.push(e);
  }
  // Concur often renders the caption in one div and the input in a sibling div.
  // Walk upward from an exact caption until one nearby editable control is found.
  const byCaption=[];
  const captions=[...root.querySelectorAll('*')].filter(e => {
    if (!visible(e)) return false;
    const t=norm(e.innerText).toLowerCase();
    if (!wants.some(w => w && t===w)) return false;
    return ![...e.children].some(c => visible(c) && norm(c.innerText).toLowerCase()===t);
  });
  for (const caption of captions) {
    let box=caption;
    for (let depth=0; box && box!==root && depth<6; depth++,box=box.parentElement) {
      const nearby=[...box.querySelectorAll('input,textarea,[contenteditable="true"]')]
        .filter(e=>visible(e)&&!e.disabled&&e.type!=='file');
      if (nearby.length===1) { byCaption.push(nearby[0]); break; }
      if (nearby.length>1) break;
    }
  }
  const hits=[...new Set([...byLabel,...byAttr,...byCaption])];
  if (hits.length!==1) return null;
  document.querySelectorAll('[data-auto-mileage-field]').forEach(e=>e.removeAttribute('data-auto-mileage-field'));
  hits[0].setAttribute('data-auto-mileage-field','1');
  return '[data-auto-mileage-field="1"]';
}"""

COMBO_JS = "(names) => {" + SURFACE_HELPERS + r"""
  const root=surface(), wants=names.map(x=>norm(x).toLowerCase());
  const candidates=[...root.querySelectorAll('[role="combobox"],button,input')]
    .filter(e => visible(e) && !e.disabled && e.getAttribute('data-testid')!=='column-sort');
  const byOwn=candidates.filter(e=>{
    const hay=norm([e.innerText,e.getAttribute('aria-label'),e.getAttribute('data-nuiexp'),e.id,e.name,e.placeholder].join(' ')).toLowerCase();
    return wants.some(w=>w && hay.includes(w));
  });
  const byLabel=[], byCaption=[];
  for (const l of root.querySelectorAll('label')) {
    const t=norm(l.innerText).toLowerCase();
    if (!wants.some(w=>w && (t===w || t.includes(w)))) continue;
    let e=l.control;
    if (!e && l.htmlFor) e=document.getElementById(l.htmlFor);
    if (!e) e=l.parentElement?.querySelector('[role="combobox"],button,input');
    if (e && root.contains(e) && visible(e) && e.getAttribute('data-testid')!=='column-sort') byLabel.push(e);
  }
  const captions=[...root.querySelectorAll('*')].filter(e => {
    if (!visible(e)) return false;
    const t=norm(e.innerText).toLowerCase();
    if (!wants.some(w => w && t===w)) return false;
    return ![...e.children].some(c => visible(c) && norm(c.innerText).toLowerCase()===t);
  });
  for (const caption of captions) {
    let box=caption;
    for (let depth=0; box && box!==root && depth<6; depth++,box=box.parentElement) {
      const nearby=[...box.querySelectorAll('[role="combobox"],button,input')]
        .filter(e=>visible(e)&&!e.disabled&&e.getAttribute('data-testid')!=='column-sort');
      if (nearby.length===1) { byCaption.push(nearby[0]); break; }
      if (nearby.length>1) break;
    }
  }
  const hits=[...new Set([...byLabel,...byOwn,...byCaption])];
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-combo]').forEach(e=>e.removeAttribute('data-auto-mileage-combo'));
  hits[0].setAttribute('data-auto-mileage-combo','1');
  return '[data-auto-mileage-combo="1"]';
}"""

OPTION_JS = r"""(wanted) => {
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const hits=[...document.querySelectorAll('[role="option"],li,button')].filter(e=>visible(e)&&norm(e.innerText)===wanted);
  const targets=[...new Set(hits.map(e=>e.closest('[role="option"],li,button')||e))];
  if(targets.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-option]').forEach(e=>e.removeAttribute('data-auto-mileage-option'));
  targets[0].setAttribute('data-auto-mileage-option','1');
  return '[data-auto-mileage-option="1"]';
}"""

FILE_INPUT_JS = "() => {" + SURFACE_HELPERS + r"""
  const root=surface();
  const hits=[...root.querySelectorAll('input[type="file"]')].filter(e=>!e.disabled);
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-file]').forEach(e=>e.removeAttribute('data-auto-mileage-file'));
  hits[0].setAttribute('data-auto-mileage-file','1');
  return '[data-auto-mileage-file="1"]';
}"""



PRE_SAVE_UI_MARKERS = (
    'data-auto-mileage-combo',
    '수동으로 경비 생성 표시',
    '자동차 마일리지 유형 표시',
    '마일리지 상세 입력 화면',
    '입력칸을 하나로 확인하지 못했습니다',
    '차량 ID 선택칸을 확인하지 못했습니다',
    '지도 이미지 업로드',
)


def _pre_save_review(row):
    return (row.get('concur_state') == 'needs_review'
            and any(marker in str(row.get('concur_note') or '') for marker in PRE_SAVE_UI_MARKERS))


def status(folder: Path, limit=None):
    book = MileageBook(folder)
    pending = [r['id'] for r in book.rows if r.get('concur_state') not in ('verified','needs_review')]
    if limit is not None:
        pending = pending[:limit]
    return {
        'total': len(book.rows),
        'pending_ids': pending,
        'pending': len(pending),
        'verified': sum(r.get('concur_state') == 'verified' for r in book.rows),
        'needs_review': sum(r.get('concur_state') == 'needs_review' and not _pre_save_review(r)
                            for r in book.rows),
        'reconcile': sum(_pre_save_review(r) for r in book.rows),
    }


def _reconcile_pre_save_reviews(page, report_url, book):
    """Retry old UI failures only after proving no expense ID exists in the report."""
    candidates=[r for r in book.rows if _pre_save_review(r)]
    if not candidates:
        return 0, 0
    page.goto(report_url, wait_until='domcontentloaded')
    check_context(page, report_url)
    ids=_report_ids(page)
    retryable=blocked=0
    changed=False
    for row in candidates:
        ident=str(row.get('concur_expense_id') or '').strip()
        if ident and ident in ids:
            blocked += 1
            row['concur_note'] = '저장 전 오류 뒤 Concur 리포트에 경비 ID가 존재하여 자동 재생성하지 않음'
            changed=True
        else:
            row.pop('concur_expense_id', None)
            row['concur_state']='retryable'
            row['concur_note']='저장 전 UI 오류였고 현재 리포트에 해당 신규 경비 ID가 없어 재시도 가능'
            retryable += 1
            changed=True
    if changed:
        book.rows=book.validate_rows(book.rows)
        book.save()
    return retryable, blocked


def _click_unique_text(page, labels, what, timeout=10000):
    selector = ui.wait_condition(page, EXACT_TEXT_JS, what + ' 표시', labels, timeout=timeout)
    page.locator(selector).click(timeout=timeout)


def _select_mileage_type(page):
    selector = ui.wait_condition(page, MILEAGE_TYPE_JS, '자동차 마일리지 유형 표시', timeout=10000)
    page.locator(selector).click(timeout=10000)
    ui.wait_condition(page, MILEAGE_FORM_READY_JS, '마일리지 상세 입력 화면', timeout=15000)


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
    selector = page.evaluate(FILE_INPUT_JS)
    if not selector:
        buttons = page.locator('[data-nuiexp="rcpt-btn-attach-receipt"]')
        if buttons.count() == 1:
            buttons.click()
            page.wait_for_timeout(300)
            selector = page.evaluate(FILE_INPUT_JS)
    if not selector:
        raise MileageConcurError('현재 마일리지 입력 화면에서 지도 이미지 업로드 칸을 하나로 확인하지 못했습니다.'
                                 + ui.diagnose(page, '마일리지 지도 첨부'))
    page.locator(selector).set_input_files(str(filename))


def _expense_id(url):
    m = re.search(r'/expenses/([^/?#]+)', str(url))
    return m.group(1) if m else None


def _report_ids(page):
    return {r.expense_id for r in ar.rows_when_ready(page, allow_incomplete=True) if r.expense_id}


def _raise_pre_save(page, report_url, before, draft_id, exc):
    """Classify a failure before Save by comparing the report before/after."""
    try:
        page.goto(report_url, wait_until='domcontentloaded')
        check_context(page, report_url)
        after=_report_ids(page)
    except Exception as verify_exc:
        raise UncertainMileageCreate(
            f'{exc} / 저장 전 실패 후 리포트 상태도 확인하지 못했습니다: {verify_exc}', draft_id) from exc
    added=after-set(before)
    if draft_id and draft_id in after:
        raise UncertainMileageCreate(str(exc), draft_id) from exc
    if len(added)==1:
        raise UncertainMileageCreate(str(exc), next(iter(added))) from exc
    if not added:
        raise MileageConcurError(str(exc)) from exc
    raise UncertainMileageCreate(
        f'{exc} / 저장 전 실패 후 신규 경비가 {len(added)}건 보여 자동 재시도하지 않습니다.') from exc


def create_one(page, report_url, row, map_path, before_ids=None):
    """Create exactly one mileage expense and prove one new report id appeared."""
    page.goto(report_url, wait_until='domcontentloaded')
    check_context(page, report_url)
    before = _report_ids(page) if before_ids is None else set(before_ids)

    # These two menu actions do not create an expense yet. Failures here are safe
    # to retry and must not be recorded as an uncertain server write.
    ui.click_target(page, ADD_EXPENSE_JS, '경비 추가')
    page.wait_for_timeout(200)
    _click_unique_text(
        page,
        ['수동으로 경비 생성','Create Expense Manually','Create Manually','Create New Expense'],
        '수동으로 경비 생성',
    )
    page.wait_for_timeout(300)
    try:
        _select_mileage_type(page)
    except Exception as exc:
        _raise_pre_save(page, report_url, before, _expense_id(page.url), exc)

    draft_id = _expense_id(page.url)
    try:
        _fill(page, ['거래 날짜','Transaction Date','Date'], row['date'], '거래 날짜')
        _fill(page, ['출발지','출발 위치','Origin','From'], row['origin'], '출발지')
        _fill(page, ['도착지','도착 위치','Destination','To'], row['destination'], '도착지')
        _select_vehicle(page, row['vehicle'])
        _fill(page, ['거리','Distance'], row['distance'], '거리')
        _fill(page, ['탑승자 수','Passengers','Passenger Count'], row['passengers'], '탑승자 수')
        _fill(page, ['설명','Description','Business Purpose'], row['description'], '설명')
        _upload_map(page, map_path)
    except Exception as exc:
        _raise_pre_save(page, report_url, before, draft_id, exc)

    # From the save click onward the server may have accepted the expense even if
    # the response/navigation is lost. Never retry automatically after this point.
    try:
        page.wait_for_timeout(700)
        ui.click_target(page, ui.SAVE_BUTTONS_JS, '마일리지 저장', '저장,Save')
    except Exception as exc:
        raise UncertainMileageCreate(str(exc), draft_id) from exc

    try:
        page.wait_for_timeout(1000)
        url_id = _expense_id(page.url)
        page.goto(report_url, wait_until='domcontentloaded')
        check_context(page, report_url)
        after = _report_ids(page)
        if draft_id and draft_id in after:
            ident = draft_id
        elif url_id and url_id in after:
            ident = url_id
        else:
            added = after - before
            ident = next(iter(added)) if len(added) == 1 else None
        if not ident:
            raise MileageConcurError(
                '저장 후 새 경비 ID를 하나로 확인하지 못했습니다.' + ui.diagnose(page, '마일리지 저장 후 확인'))
        return ident
    except Exception as exc:
        raise UncertainMileageCreate(str(exc), draft_id or _expense_id(page.url)) from exc


def run_in_session(page, report_url, folder: Path, apply=True, limit=None):
    """Process saved mileage rows in an already authenticated report session."""
    book = MileageBook(folder)
    if book.dirty:
        raise MileageConcurError('마일리지 표에 저장하지 않은 변경이 있습니다. 먼저 저장하세요.')

    info = status(folder, limit)
    if apply and info.get('reconcile'):
        retryable, blocked = _reconcile_pre_save_reviews(page, report_url, book)
        if retryable:
            print(f'이전 저장 전 UI 실패 {retryable}건: 현재 리포트에 신규 경비 ID가 없어 안전하게 다시 시도합니다.')
        if blocked:
            print(f'이전 저장 전 UI 실패 {blocked}건: Concur 리포트에 경비 ID가 있어 자동 재생성하지 않습니다.')
        info = status(folder, limit)

    pending_ids = list(info['pending_ids'])
    if info['verified']:
        print(f"이미 Concur 생성 확인된 마일리지 {info['verified']}건은 건너뜁니다.")
    if info['needs_review']:
        print(f"이전 실행에서 확인이 필요한 마일리지 {info['needs_review']}건은 자동 재시도하지 않습니다.")
    if not apply and info.get('reconcile'):
        print(f"저장 전 UI 실패 {info['reconcile']}건은 실제 리포트 확인 후 재시도 가능 여부를 결정합니다.")

    if not pending_ids:
        summary = (f"마일리지 신규 생성 대상 없음 · 기존 확인 {info['verified']}건"
                   + (f" · 확인 필요 {info['needs_review']}건" if info['needs_review'] else ''))
        print(summary)
        return RunResult(int(bool(info['needs_review'])), summary)

    if not apply:
        summary = f"미리보기: Concur 신규 마일리지 {len(pending_ids)}건"
        print(summary)
        return RunResult(0, summary)

    created = 0
    for n, local_id in enumerate(pending_ids, 1):
        # Reload by stable local id after every save; save() replaces validated dicts.
        current = next((r for r in book.rows if r.get('id') == local_id), None)
        if current is None:
            raise MileageConcurError('마일리지 내부 식별자가 실행 중 사라졌습니다.')
        row = checked_row(current)
        image = book.check_image(row)
        print(f'[{n}/{len(pending_ids)}] 마일리지 신규 생성: '
              f'{row["date"]} · {row["origin"]} → {row["destination"]} · {row["distance"]}km')
        try:
            ident = create_one(page, report_url, row, image)
        except UncertainMileageCreate as exc:
            current['concur_state'] = 'needs_review'
            current['concur_note'] = str(exc)
            if exc.expense_id:
                current['concur_expense_id'] = exc.expense_id
            book.rows = book.validate_rows(book.rows)
            book.save()
            summary = ('마일리지 저장 결과 확인 필요 1건 · 자동 재생성하지 않습니다. '
                       'Concur에서 해당 경비를 확인해 주세요.')
            print('  ' + summary)
            print('  ' + str(exc))
            return RunResult(1, summary)
        except MileageConcurError as exc:
            current['concur_state'] = 'retryable'
            current['concur_note'] = str(exc)
            book.rows = book.validate_rows(book.rows)
            book.save()
            summary = '마일리지 저장 전 화면 인식 실패 1건 · Concur 저장은 누르지 않았습니다. 다시 실행할 수 있습니다.'
            print('  ' + summary)
            print('  ' + str(exc))
            return RunResult(1, summary)

        current['concur_state'] = 'verified'
        current['concur_expense_id'] = ident
        current['concur_note'] = '저장 후 리포트의 신규 경비 ID 확인'
        book.rows = book.validate_rows(book.rows)
        book.save()
        created += 1
        print(f'  확인 완료: {ident}')

    summary = (f'마일리지 신규 생성 확인 {created}건 · 기존 확인 {info["verified"]}건'
               + (f' · 기존 확인 필요 {info["needs_review"]}건' if info['needs_review'] else ''))
    print(summary)
    return RunResult(int(bool(info['needs_review'])), summary)


def run(folder: Path, apply=True, limit=None):
    info = status(folder, limit)
    if not info['pending'] and not (apply and info.get('reconcile')):
        # No browser is needed merely to report verified/review states.
        return run_in_session(None, '', folder, apply, limit)
    if not apply:
        return RunResult(0, f"미리보기: Concur 신규 마일리지 {info['pending']}건")

    pw, ctx, page, report_url = ar.open_report()
    try:
        return run_in_session(page, report_url, folder, True, limit)
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
