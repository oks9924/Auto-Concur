"""Create locally prepared vehicle-mileage rows as new Concur expenses.

Mileage is executed after card expenses in the same report/browser session. A local
row has a stable id. Verified rows and uncertain writes are never automatically
created again.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout

from . import attach_receipts as ar, console, paths
from . import concur_ui as ui
from .concur_workflow import RunResult
from .mileage import MileageBook, checked_row
from .report_session import check_context


class MileageConcurError(Exception):
    pass


class RetryableMileageCreate(MileageConcurError):
    """The Save action was never attempted; a provisional URL id is not proof of a server write."""
    def __init__(self, message, draft_id=None):
        super().__init__(message)
        self.draft_id = draft_id


class UncertainMileageCreate(MileageConcurError):
    """The Save action may have reached Concur; automatic recreation is unsafe."""
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
      '[role="dialog"],[role="alertdialog"],.sapcnqr-dialog__body,#sapcnqr-layout-side-panel-elements,[class*="side-panel__side"]'
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
  const controls=()=>[...root.querySelectorAll('input,textarea')].filter(e =>
    visible(e) && !e.disabled && e.type!=='file' && e.type!=='hidden');
  const textMatches=[...root.querySelectorAll('*')].filter(e => {
    if(!visible(e)) return false;
    const t=norm(e.innerText).toLowerCase();
    return wants.includes(t) && ![...e.children].some(c=>visible(c)&&wants.includes(norm(c.innerText).toLowerCase()));
  });

  // First use the real visual field structure: exact label text -> nearest ancestor
  // containing exactly one editable control. Concur often renders labels as div/span,
  // not HTML <label>.
  const byVisual=[];
  for(const label of textMatches){
    let n=label.parentElement;
    for(let depth=0;n&&root.contains(n)&&depth<7;depth++,n=n.parentElement){
      const hits=[...n.querySelectorAll('input,textarea')].filter(e =>
        visible(e)&&!e.disabled&&e.type!=='file'&&e.type!=='hidden');
      if(hits.length===1){ byVisual.push([hits[0],100-depth]); break; }
      if(hits.length>4) break;
    }
  }

  const byAttr=controls().map(e=>{
    const hay=[e.getAttribute('aria-label'),e.name,e.id,e.placeholder,e.getAttribute('data-nuiexp')]
      .map(x=>norm(x).toLowerCase()).join(' ');
    return [e,wants.some(w=>w&&hay.includes(w))?80:0];
  }).filter(x=>x[1]);

  const scored=[...byVisual,...byAttr];
  if(!scored.length)return null;
  const score=new Map();
  for(const [e,v] of scored) score.set(e,Math.max(score.get(e)||0,v));
  const best=Math.max(...score.values());
  const hits=[...score].filter(x=>x[1]===best).map(x=>x[0]);
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-field]').forEach(e=>e.removeAttribute('data-auto-mileage-field'));
  hits[0].setAttribute('data-auto-mileage-field','1');
  return '[data-auto-mileage-field="1"]';
}"""

COMBO_JS = "(names) => {" + SURFACE_HELPERS + r"""
  const root=surface(), wants=names.map(x=>norm(x).toLowerCase());
  const candidates=()=>[...root.querySelectorAll('[role="combobox"],button,input')].filter(e =>
    visible(e)&&!e.disabled&&e.getAttribute('data-testid')!=='column-sort'
    && e.type!=='hidden'&&e.type!=='file');
  const textMatches=[...root.querySelectorAll('*')].filter(e=>{
    if(!visible(e))return false;
    const t=norm(e.innerText).toLowerCase();
    return wants.includes(t) && ![...e.children].some(c=>visible(c)&&wants.includes(norm(c.innerText).toLowerCase()));
  });
  const byVisual=[];
  for(const label of textMatches){
    let n=label.parentElement;
    for(let depth=0;n&&root.contains(n)&&depth<7;depth++,n=n.parentElement){
      const hits=[...n.querySelectorAll('[role="combobox"],button,input')].filter(e =>
        visible(e)&&!e.disabled&&e.getAttribute('data-testid')!=='column-sort'
        && e.type!=='hidden'&&e.type!=='file');
      if(hits.length===1){byVisual.push([hits[0],100-depth]);break;}
      if(hits.length>5)break;
    }
  }
  const byAttr=candidates().map(e=>{
    const hay=norm([e.innerText,e.getAttribute('aria-label'),e.getAttribute('data-nuiexp'),e.id,e.name,e.placeholder].join(' ')).toLowerCase();
    return [e,wants.some(w=>w&&hay.includes(w))?80:0];
  }).filter(x=>x[1]);
  const scored=[...byVisual,...byAttr];
  if(!scored.length)return null;
  const score=new Map();
  for(const [e,v] of scored)score.set(e,Math.max(score.get(e)||0,v));
  const best=Math.max(...score.values());
  const hits=[...score].filter(x=>x[1]===best).map(x=>x[0]);
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-combo]').forEach(e=>e.removeAttribute('data-auto-mileage-combo'));
  hits[0].setAttribute('data-auto-mileage-combo','1');
  return '[data-auto-mileage-combo="1"]';
}"""

OPTION_JS = r"""(wanted) => {
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
  const norm=s=>(s||'').replace(/\s+/g,' ').trim();
  const all=[...document.querySelectorAll('[role="option"],li,button')].filter(visible);
  const exact=all.filter(e=>norm(e.innerText)===wanted);
  const pref=all.filter(e=>norm(e.innerText).startsWith(wanted+' '));
  const hits=exact.length===1?exact:(exact.length===0&&pref.length===1?pref:[]);
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

RECEIPT_VIEW_JS = "(names) => {" + SURFACE_HELPERS + r"""
  const root=surface(), wants=names.map(norm);
  const buttons=[...root.querySelectorAll('button,[role="button"],a')].filter(e=>visible(e)&&!e.disabled);
  const hits=buttons.filter(e=>{
    const text=norm(e.innerText), aria=norm(e.getAttribute('aria-label'));
    return wants.includes(text)||wants.includes(aria);
  });
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-receipt-view]').forEach(e=>e.removeAttribute('data-auto-mileage-receipt-view'));
  hits[0].setAttribute('data-auto-mileage-receipt-view','1');
  return '[data-auto-mileage-receipt-view="1"]';
}"""

RECEIPT_ATTACH_JS = "() => {" + SURFACE_HELPERS + r"""
  const root=surface();
  const buttons=[...root.querySelectorAll('button,[role="button"]')].filter(e=>visible(e)&&!e.disabled);
  const hits=buttons.filter(e=>{
    const text=norm(e.innerText), aria=norm(e.getAttribute('aria-label'));
    const hook=e.getAttribute('data-nuiexp')||'';
    return hook==='rcpt-btn-attach-receipt'
      || ['영수증 첨부','Attach Receipt','Attach receipt'].includes(text)
      || ['영수증 첨부','Attach Receipt','Attach receipt'].includes(aria);
  });
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-receipt-attach]').forEach(e=>e.removeAttribute('data-auto-mileage-receipt-attach'));
  hits[0].setAttribute('data-auto-mileage-receipt-attach','1');
  return '[data-auto-mileage-receipt-attach="1"]';
}"""

GLOBAL_UPLOAD_JS = r"""() => {
  const byId=[...document.querySelectorAll('#upload-file')].filter(e=>!e.disabled);
  if(byId.length!==1)return null;
  byId[0].setAttribute('data-auto-mileage-file-global','1');
  return '[data-auto-mileage-file-global="1"]';
}"""

RECEIPT_CLOSE_JS = "() => {" + SURFACE_HELPERS + r"""
  const root=surface();
  const buttons=[...root.querySelectorAll('button,[role="button"]')].filter(e=>visible(e)&&!e.disabled);
  const hits=buttons.filter(e=>{
    const text=norm(e.innerText), aria=norm(e.getAttribute('aria-label'));
    return ['닫기','Close'].includes(text)||['닫기','Close'].includes(aria);
  });
  if(hits.length!==1)return null;
  document.querySelectorAll('[data-auto-mileage-receipt-close]').forEach(e=>e.removeAttribute('data-auto-mileage-receipt-close'));
  hits[0].setAttribute('data-auto-mileage-receipt-close','1');
  return '[data-auto-mileage-receipt-close="1"]';
}"""


KNOWN_PREWRITE_MARKERS = (
    'data-auto-mileage-combo',
    '수동으로 경비 생성 표시',
    '자동차 마일리지 유형 표시',
    '입력칸을 하나로 확인하지 못했습니다',
    '차량 ID 선택칸을 확인하지 못했습니다',
    '마일리지 상세 입력 화면',
    '이전에 생성된 것으로 기록한 마일리지 경비 ID를 현재 리포트에서 찾지 못했습니다',
)


def _legacy_presave(row):
    return (row.get('concur_state') == 'needs_review'
            and any(marker in str(row.get('concur_note') or '') for marker in KNOWN_PREWRITE_MARKERS))


def _retryable(row):
    return row.get('concur_state') == 'retryable' or row.get('concur_stage') == 'pre_save' or _legacy_presave(row)


def _receipt_pending(row):
    return (bool(row.get('concur_expense_id'))
            and row.get('concur_state') in ('verified', 'receipt_missing', 'receipt_unknown')
            and row.get('concur_receipt_verified') is not True)


def status(folder: Path, limit=None):
    book = MileageBook(folder)
    retryable = [r['id'] for r in book.rows if _retryable(r)]
    receipt_pending = [r['id'] for r in book.rows if _receipt_pending(r)]
    pending = [r['id'] for r in book.rows
               if r.get('concur_state') not in ('verified','needs_review')
               or _retryable(r) or _receipt_pending(r)]
    if limit is not None:
        pending = pending[:limit]
    return {
        'total': len(book.rows),
        'pending_ids': pending,
        'pending': len(pending),
        'verified': sum(r.get('concur_state') == 'verified'
                        and r.get('concur_receipt_verified') is True for r in book.rows),
        'created_receipt_check': len(receipt_pending),
        'needs_review': sum(r.get('concur_state') == 'needs_review' and not _retryable(r)
                            for r in book.rows),
        'retryable_prewrite': len(retryable),
    }


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
    # In the observed Concur form the car popper can remain above the detail
    # panel even after a choice is applied. Never continue while it can intercept
    # receipt/save clicks.
    popper = page.locator('[data-nuiexp="popper-carKey"]')
    if popper.count():
        try:
            popper.wait_for(state='hidden', timeout=800)
        except PWTimeout:
            page.keyboard.press('Escape')
            try:
                popper.wait_for(state='hidden', timeout=2000)
            except PWTimeout as exc:
                raise MileageConcurError(
                    '차량 ID 선택 목록이 닫히지 않아 다음 단계로 진행하지 않았습니다.'
                    + ui.diagnose(page, '마일리지 차량 목록 닫기')) from exc


def _upload_map(page, filename):
    # Do not use the report-list receipt button behind the expense side panel.
    # Open the receipt UI from the active mileage surface first.
    selector = page.evaluate(FILE_INPUT_JS)
    uploaded = False
    if not selector:
        view = page.evaluate(
            RECEIPT_VIEW_JS,
            ['영수증 보기','View Receipt','View Receipts','Receipts'],
        )
        if view:
            page.locator(view).click(timeout=10000)
            page.wait_for_timeout(350)

        selector = page.evaluate(FILE_INPUT_JS) or page.evaluate(GLOBAL_UPLOAD_JS)

    if not selector:
        attach = page.evaluate(RECEIPT_ATTACH_JS)
        if attach:
            chooser = None
            try:
                with page.expect_file_chooser(timeout=1500) as pending:
                    page.locator(attach).click(timeout=5000)
                chooser = pending.value
            except PWTimeout:
                # Some Concur builds reveal the hidden input instead of opening
                # the native chooser. The click already happened; do not click twice.
                pass
            if chooser is not None:
                chooser.set_files(str(filename))
                uploaded = True
            else:
                page.wait_for_timeout(300)
                selector = page.evaluate(FILE_INPUT_JS) or page.evaluate(GLOBAL_UPLOAD_JS)

    if not uploaded:
        if not selector:
            raise MileageConcurError(
                '현재 마일리지 영수증 화면에서 지도 이미지 업로드 칸을 확인하지 못했습니다.'
                + ui.diagnose(page, '마일리지 지도 첨부'))
        page.locator(selector).set_input_files(str(filename))
        uploaded = True

    page.wait_for_timeout(3500)

    # If a receipt modal/drawer replaced the detail surface, close only that
    # surface and prove the mileage form is visible again before Save.
    if not page.evaluate(MILEAGE_FORM_READY_JS):
        close = page.evaluate(RECEIPT_CLOSE_JS)
        if close:
            page.locator(close).click(timeout=10000)
            ui.wait_condition(page, MILEAGE_FORM_READY_JS, '마일리지 상세 화면 복귀', timeout=10000)
        elif not page.evaluate(MILEAGE_FORM_READY_JS):
            raise MileageConcurError(
                '지도 이미지는 선택했지만 마일리지 상세 화면으로 돌아오지 못해 저장하지 않았습니다.'
                + ui.diagnose(page, '마일리지 영수증 화면 닫기'))


def _expense_id(url):
    m = re.search(r'/expenses/([^/?#]+)', str(url))
    return m.group(1) if m else None


def _report_ids(page, strict=False):
    rows = ar.rows_when_ready(page, allow_incomplete=not strict)
    return {r.expense_id for r in rows if r.expense_id}


def _receipt_state(page, report_url, expense_id, tries=5):
    """Return True/False only when one report row gives a stable receipt answer."""
    seen = []
    for attempt in range(tries):
        page.goto(report_url, wait_until='domcontentloaded')
        check_context(page, report_url)
        rows = ar.rows_when_ready(page, allow_incomplete=True)
        matches = [r for r in rows if r.expense_id == expense_id]
        if len(matches) != 1:
            return None
        state = matches[0].has_receipt
        if state is True:
            return True
        seen.append(state)
        if attempt + 1 < tries:
            page.wait_for_timeout(1200)
    return False if seen and all(value is False for value in seen) else None


def _repair_receipt(page, report_url, row, map_path, expense_id):
    """Attach only the map receipt to an already-created expense. Never create a new expense."""
    page.goto(ar.expense_url(report_url, expense_id), wait_until='domcontentloaded')
    check_context(page, report_url)
    ui.wait_condition(page, MILEAGE_FORM_READY_JS, '기존 마일리지 상세 입력 화면', timeout=15000)
    _upload_map(page, map_path)
    try:
        ui.click_target(page, ui.SAVE_BUTTONS_JS, '마일리지 영수증 저장',
                        '경비 저장,저장,Save Expense,Save')
    except Exception as exc:
        raise UncertainMileageCreate(str(exc), expense_id) from exc
    page.wait_for_timeout(1500)
    return _receipt_state(page, report_url, expense_id)


def _fill_and_save(page, report_url, row, map_path, draft_id, before):
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
        # No Save click happened. A URL id at this point is only provisional.
        # Keep it so a later run can reuse it if it really appears in the report,
        # but never classify this as an uncertain server write.
        raise RetryableMileageCreate(str(exc), draft_id) from exc

    try:
        page.wait_for_timeout(700)
        ui.click_target(page, ui.SAVE_BUTTONS_JS, '마일리지 저장', '경비 저장,저장,Save Expense,Save')
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


def create_one(page, report_url, row, map_path, before_ids=None):
    """Create exactly one mileage expense and prove one new report id appeared."""
    page.goto(report_url, wait_until='domcontentloaded')
    check_context(page, report_url)
    before = _report_ids(page) if before_ids is None else set(before_ids)

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
        raise RetryableMileageCreate(str(exc), _expense_id(page.url)) from exc

    draft_id = _expense_id(page.url)
    return _fill_and_save(page, report_url, row, map_path, draft_id, before)


def run_in_session(page, report_url, folder: Path, apply=True, limit=None):
    """Process saved mileage rows in an already authenticated report session."""
    book = MileageBook(folder)
    if book.dirty:
        raise MileageConcurError('마일리지 표에 저장하지 않은 변경이 있습니다. 먼저 저장하세요.')

    info = status(folder, limit)
    pending_ids = list(info['pending_ids'])
    if info['verified']:
        print(f"이미 Concur 생성 확인된 마일리지 {info['verified']}건은 건너뜁니다.")
    if info['needs_review']:
        print(f"이전 실행에서 확인이 필요한 마일리지 {info['needs_review']}건은 자동 재시도하지 않습니다.")
    if info.get('retryable_prewrite'):
        print(f"저장 전 화면 인식 실패 이력 {info['retryable_prewrite']}건은 실제 저장 여부를 확인한 뒤 안전하게 다시 시도합니다.")
    if info.get('created_receipt_check'):
        print(f"이미 생성된 마일리지 {info['created_receipt_check']}건은 새로 만들지 않고 영수증 첨부 상태를 확인합니다.")

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
        if _receipt_pending(current):
            expense_id = current.get('concur_expense_id')
            row = checked_row(current)
            image = book.check_image(row)
            print(f'[{n}/{len(pending_ids)}] 기존 마일리지 영수증 확인: {expense_id}')
            state = _receipt_state(page, report_url, expense_id)
            if state is True:
                current['concur_state'] = 'verified'
                current['concur_stage'] = 'verified'
                current['concur_receipt_verified'] = True
                current['concur_note'] = '기존 경비와 영수증 존재 확인'
                book.rows = book.validate_rows(book.rows)
                book.save()
                created += 1
                print('  기존 영수증 확인 완료 · 새 경비 생성 안 함')
                continue
            if state is None:
                current['concur_state'] = 'receipt_unknown'
                current['concur_stage'] = 'receipt_check'
                current['concur_note'] = '현재 리포트에서 영수증 상태를 하나로 확인하지 못함'
                book.rows = book.validate_rows(book.rows)
                book.save()
                print('  영수증 상태 미확인 · 중복 첨부하지 않고 다음 실행에서 다시 확인합니다.')
                return RunResult(1, '기존 마일리지 영수증 상태 확인 필요 1건')
            print('  기존 경비에 영수증이 없는 것을 확인했습니다. 같은 경비 ID에 지도 이미지만 첨부합니다.')
            try:
                repaired = _repair_receipt(page, report_url, row, image, expense_id)
            except UncertainMileageCreate as exc:
                current['concur_state'] = 'receipt_unknown'
                current['concur_stage'] = 'receipt_save_attempted'
                current['concur_note'] = str(exc)
                current['concur_receipt_verified'] = False
                book.rows = book.validate_rows(book.rows)
                book.save()
                print('  영수증 저장 결과 확인 필요 · 새 경비는 만들지 않습니다.')
                return RunResult(1, '기존 마일리지 영수증 저장 결과 확인 필요 1건')
            if repaired is True:
                current['concur_state'] = 'verified'
                current['concur_stage'] = 'verified'
                current['concur_receipt_verified'] = True
                current['concur_note'] = '기존 경비에 지도 이미지 첨부 후 영수증 존재 확인'
                book.rows = book.validate_rows(book.rows)
                book.save()
                created += 1
                print('  지도 이미지 첨부 및 영수증 확인 완료')
                continue
            current['concur_state'] = 'receipt_missing' if repaired is False else 'receipt_unknown'
            current['concur_stage'] = 'receipt_check'
            current['concur_receipt_verified'] = False
            current['concur_note'] = '첨부 후 리포트에서 영수증 존재를 확인하지 못함'
            book.rows = book.validate_rows(book.rows)
            book.save()
            print('  첨부 후 영수증을 확인하지 못했습니다. 새 경비는 만들지 않습니다.')
            return RunResult(1, '기존 마일리지 영수증 확인 필요 1건')

        # Migrate old pre-save failures. Earlier builds stored a provisional URL id
        # as concur_expense_id even though Save was never clicked.
        if _legacy_presave(current):
            provisional = current.get('concur_draft_id') or current.get('concur_expense_id')
            current['concur_state'] = 'retryable'
            current['concur_stage'] = 'pre_save'
            current['concur_note'] = '이전 버전의 저장 전 실패를 안전 재시도 상태로 복구'
            current.pop('concur_expense_id', None)
            if provisional:
                current['concur_draft_id'] = provisional
            book.rows = book.validate_rows(book.rows)
            book.save()
            current = next(r for r in book.rows if r.get('id') == local_id)

        row = checked_row(current)
        image = book.check_image(row)
        print(f'[{n}/{len(pending_ids)}] 마일리지 신규 생성: '
              f'{row["date"]} · {row["origin"]} → {row["destination"]} · {row["distance"]}km')
        try:
            provisional = current.get('concur_draft_id')
            if provisional:
                page.goto(report_url, wait_until='domcontentloaded')
                check_context(page, report_url)
                ids = _report_ids(page, strict=True)
                if provisional in ids:
                    print(f'  저장 전 생성된 기존 초안 {provisional}을 이어서 입력합니다.')
                    page.goto(ar.expense_url(report_url, provisional), wait_until='domcontentloaded')
                    check_context(page, report_url)
                    ui.wait_condition(page, MILEAGE_FORM_READY_JS, '기존 마일리지 상세 입력 화면', timeout=15000)
                    ident = _fill_and_save(page, report_url, row, image, provisional, ids)
                else:
                    print('  이전 임시 경비 ID가 현재 리포트에 없어 저장된 경비가 아닌 것으로 확인했습니다. 새로 생성합니다.')
                    current.pop('concur_draft_id', None)
                    book.rows = book.validate_rows(book.rows)
                    book.save()
                    current = next(r for r in book.rows if r.get('id') == local_id)
                    ident = create_one(page, report_url, row, image)
            else:
                ident = create_one(page, report_url, row, image)
        except RetryableMileageCreate as exc:
            current['concur_state'] = 'retryable'
            current['concur_stage'] = 'pre_save'
            current['concur_note'] = str(exc)
            current.pop('concur_expense_id', None)
            if exc.draft_id:
                current['concur_draft_id'] = exc.draft_id
            else:
                current.pop('concur_draft_id', None)
            book.rows = book.validate_rows(book.rows)
            book.save()
            summary = '마일리지 저장 전 화면 인식 실패 1건 · 저장 버튼은 누르지 않았으므로 다시 실행할 수 있습니다.'
            print('  ' + summary)
            print('  ' + str(exc))
            return RunResult(1, summary)
        except UncertainMileageCreate as exc:
            current['concur_state'] = 'needs_review'
            current['concur_stage'] = 'save_attempted'
            current['concur_note'] = str(exc)
            current.pop('concur_draft_id', None)
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
            current['concur_stage'] = 'pre_save'
            current['concur_note'] = str(exc)
            current.pop('concur_expense_id', None)
            book.rows = book.validate_rows(book.rows)
            book.save()
            summary = '마일리지 저장 전 화면 인식 실패 1건 · Concur 저장은 누르지 않았습니다. 다시 실행할 수 있습니다.'
            print('  ' + summary)
            print('  ' + str(exc))
            return RunResult(1, summary)

        current['concur_expense_id'] = ident
        current.pop('concur_draft_id', None)
        receipt = _receipt_state(page, report_url, ident)
        if receipt is True:
            current['concur_state'] = 'verified'
            current['concur_stage'] = 'verified'
            current['concur_receipt_verified'] = True
            current['concur_note'] = '저장 후 신규 경비 ID와 영수증 존재 확인'
            book.rows = book.validate_rows(book.rows)
            book.save()
            created += 1
            print(f'  경비 및 영수증 확인 완료: {ident}')
            continue

        current['concur_state'] = 'receipt_missing' if receipt is False else 'receipt_unknown'
        current['concur_stage'] = 'receipt_check'
        current['concur_receipt_verified'] = False
        current['concur_note'] = ('경비는 생성됐지만 리포트에서 영수증이 없음'
                                  if receipt is False else '경비는 생성됐지만 영수증 상태를 확인하지 못함')
        book.rows = book.validate_rows(book.rows)
        book.save()
        print(f'  경비 생성 확인: {ident} · 영수증은 별도 확인/보완 필요')
        return RunResult(1, '마일리지 경비 생성 완료 · 영수증 확인 필요 1건')

    summary = (f'마일리지 경비·영수증 확인 {created}건 · 기존 완료 {info["verified"]}건'
               + (f' · 기존 확인 필요 {info["needs_review"]}건' if info['needs_review'] else ''))
    print(summary)
    return RunResult(int(bool(info['needs_review'])), summary)


def run(folder: Path, apply=True, limit=None):
    info = status(folder, limit)
    if not info['pending']:
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
