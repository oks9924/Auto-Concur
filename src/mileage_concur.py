"""Create and verify Concur car-mileage expenses from the separate local mileage book.

Fail-closed rules:
- A local row is bound to one Concur expense id before fields are written.
- An uncertain create/save is never repeated automatically.
- Existing receipt status is checked before a retry can upload the map again.
- Field discovery is label/hook based; ambiguous controls are not guessed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from pathlib import Path

from . import attach_receipts as ar, concur_ui, fix_expenses as fx
from .concur_formats import DATE_ORDER, placeholder_order
from .concur_values import clean, resolve_dates
from .concur_workflow import RunResult
from .mileage import BOOK_NAME, MileageBook
from .report_session import check_context
from .worksheet import write_json

TYPE_NAMES = ('자동차 마일리지', 'Car Mileage')
BINDINGS = 'mileage-concur.json'

FIELD_SPECS = {
    'date': (('거래 날짜', 'Transaction Date'), 'input[id^="transactionDate"], input[name="transactionDate"]'),
    'origin': (('출발지', '출발 위치', 'From', 'Origin'), ''),
    'destination': (('도착지', '도착 위치', 'To', 'Destination'), ''),
    'vehicle': (('차량 ID', 'Vehicle ID', 'Vehicle'), ''),
    'distance': (('거리', '거리 (km)', 'Distance', 'Distance (km)'), ''),
    'passengers': (('탑승자 수', '동승자 수', 'Passengers', 'Number of Passengers'), ''),
    'description': (('설명', 'Description'), ''),
}

MARK = """
  const mark = (el, name) => {
    document.querySelectorAll('[data-auto-mileage]').forEach(e => e.removeAttribute('data-auto-mileage'));
    if (!el) return null;
    el.setAttribute('data-auto-mileage', name || '1');
    return '[data-auto-mileage="' + (name || '1') + '"]';
  };
"""

ADD_EXPENSE_JS = "() => {" + concur_ui.DOM_HELPERS + MARK + """
  const root = surface();
  const found = root.querySelector('[data-nuiexp="add-expense-menu-button"]')
    || [...root.querySelectorAll('button')].find(b => visible(b) &&
      ['경비 추가','Add Expense'].includes((b.innerText || '').trim()));
  return mark(found, 'add');
}"""

TYPE_OPTION_JS = "(names) => {" + concur_ui.DOM_HELPERS + MARK + """
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const wants = new Set(names.map(norm));
  const root = surface();
  const nodes = [...root.querySelectorAll('button,[role="menuitem"],[role="option"],a')].filter(visible);
  const exact = nodes.filter(n => wants.has(norm(n.innerText || n.textContent)));
  return exact.length === 1 ? mark(exact[0], 'type') : exact.length > 1 ? '!AMBIGUOUS' : null;
}"""

FIELD_SELECTOR_JS = "(arg) => {" + concur_ui.DOM_HELPERS + MARK + r"""
  const root = surface();
  const norm = s => (s || '').replace(/\s+/g, ' ').replace(/s*[:：]s*$/, '').trim().toLowerCase();
  const labelOf = el => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const value = by.split(/\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ').trim();
      if (value) return value;
    }
    if (el.id) {
      const label = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (label) return label.innerText || '';
    }
    const own = el.closest('label');
    if (own) return own.innerText || '';
    const wrap = el.closest('[class*="form-field"],[class*="form-group"],[data-testid*="field"]');
    const label = wrap && wrap.querySelector('label');
    return label ? (label.innerText || '') : '';
  };
  if (arg.selector) {
    const direct = [...root.querySelectorAll(arg.selector)].filter(visible);
    if (direct.length === 1) return mark(direct[0], arg.name);
    if (direct.length > 1) return '!AMBIGUOUS';
  }
  const wants = arg.labels.map(norm);
  const controls = [...root.querySelectorAll('input,textarea,[role="combobox"]')].filter(visible);
  const matches = controls.filter(el => {
    const got = norm(labelOf(el));
    return got && wants.some(w => got === w || got.startsWith(w + ' '));
  });
  return matches.length === 1 ? mark(matches[0], arg.name) : matches.length > 1 ? '!AMBIGUOUS' : null;
}"""

FIELD_VALUE_JS = "(selector) => {" + concur_ui.DOM_HELPERS + r"""
  const el = document.querySelector(selector);
  if (!el || !visible(el)) return null;
  if ('value' in el && String(el.value || '').trim()) return String(el.value).trim();
  const input = el.querySelector && el.querySelector('input');
  if (input && String(input.value || '').trim()) return String(input.value).trim();
  return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
}"""

OPTION_JS = "(want) => {" + concur_ui.DOM_HELPERS + MARK + r"""
  const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  const root = surface(), target = norm(want);
  const options = [...root.querySelectorAll('[role="option"],li[role="option"]')]
    .filter(o => visible(o) && o.getAttribute('aria-disabled') !== 'true');
  const exact = options.filter(o => norm(o.innerText || o.textContent) === target);
  return exact.length === 1 ? mark(exact[0], 'option') : exact.length > 1 ? '!AMBIGUOUS' : null;
}"""

EXPENSE_ID_JS = """() => {
  const m = location.pathname.match(/\/expenses\/([^/?#]+)\/?$/);
  return m ? m[1] : null;
}"""


INTERMEDIATE_JS = "(names) => {" + concur_ui.DOM_HELPERS + MARK + r"""
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const wants = new Set(names.map(norm));
  const root = surface();
  const nodes = [...root.querySelectorAll('button,[role="menuitem"],a')].filter(visible);
  const exact = nodes.filter(n => wants.has(norm(n.innerText || n.textContent)));
  return exact.length === 1 ? mark(exact[0], 'intermediate') : exact.length > 1 ? '!AMBIGUOUS' : null;
}"""

CREATE_NAMES = ('새 경비 만들기', '새 경비', 'Create New Expense', 'New Expense')


def _maybe_target(page, script, arg=None, loops=12, wait_ms=200):
    for _ in range(loops):
        try:
            result = page.evaluate(script, arg)
            if result:
                return result
        except Exception:
            pass
        page.wait_for_timeout(wait_ms)
    return None


def row_fingerprint(row):
    keys = ('date','origin','destination','vehicle','kind','rate','distance','passengers','description','map_sha256')
    payload = [str(row.get(k, '')) for k in keys]
    return sha256(json.dumps(payload, ensure_ascii=False, separators=(',',':')).encode()).hexdigest()


class BindingStore:
    def __init__(self, folder):
        self.path = Path(folder) / BINDINGS
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != 1 or not isinstance(data.get('rows'), dict):
                raise ValueError(f'{BINDINGS}: 지원하지 않는 형식입니다. 자동으로 덮어쓰지 않았습니다.')
            self.data = data
        else:
            self.data = {'version': 1, 'rows': {}}

    def get(self, local_id):
        return self.data['rows'].get(local_id)

    def set(self, local_id, **values):
        current = dict(self.data['rows'].get(local_id) or {})
        current.update(values)
        self.data['rows'][local_id] = current
        write_json(self.path, self.data)
        return current


def _date_text(day, current='', placeholder=''):
    order = placeholder_order(placeholder)
    separator = '-'
    hit = re.search(r'(YYYY|MM|DD)\s*([/.-])', clean(placeholder).upper())
    if hit:
        separator = hit.group(2)
    if not order and current:
        parsed = resolve_dates([{'date': current}], order=None)[0]
        if parsed:
            # Determine only from a non-ambiguous existing value.
            parts = re.split(r'[/.-]', clean(current))
            if len(parts) == 3:
                if len(parts[0]) == 4: order = 'YMD'
                elif len(parts[2]) == 4 and int(parts[0]) > 12: order = 'DMY'
                elif len(parts[2]) == 4 and int(parts[1]) > 12: order = 'MDY'
            m = re.search(r'[/.-]', current)
            if m: separator = m.group(0)
    explicit = DATE_ORDER.get()
    if not order and explicit != 'AUTO':
        order = explicit
        separator = '-' if order == 'YMD' else '/'
    if order not in ('YMD','MDY','DMY'):
        raise ar.AttachError('마일리지 거래 날짜 입력 서식을 확인하지 못했습니다. Concur 표시 서식에서 날짜 순서를 선택해 주세요.')
    values = {'Y':f'{day.year:04d}','M':f'{day.month:02d}','D':f'{day.day:02d}'}
    return separator.join(values[c] for c in order)


def _field(page, name):
    labels, selector = FIELD_SPECS[name]
    result = concur_ui.wait_condition(page, FIELD_SELECTOR_JS, f'마일리지 {labels[0]} 입력칸',
        {'name':name,'labels':list(labels),'selector':selector}, timeout=20000)
    if result == '!AMBIGUOUS':
        raise ar.AttachError(f'마일리지 {labels[0]} 입력칸이 여러 개 보여 자동으로 고르지 않았습니다.'
                             + concur_ui.diagnose(page, f'마일리지 {labels[0]}'))
    return result


def _value(page, name):
    selector = _field(page, name)
    return page.evaluate(FIELD_VALUE_JS, selector), selector


def _fill(page, name, value):
    current, selector = _value(page, name)
    if clean(current) == clean(value):
        return False
    node = page.locator(selector)
    try:
        node.fill(str(value))
    except Exception as exc:
        raise ar.AttachError(f'{FIELD_SPECS[name][0][0]} 입력칸에 값을 넣지 못했습니다: {exc}') from exc
    page.keyboard.press('Tab')
    shown = page.evaluate(FIELD_VALUE_JS, selector)
    if clean(shown) != clean(value):
        raise ar.AttachError(f'{FIELD_SPECS[name][0][0]} 값이 입력 후 유지되지 않았습니다 (화면: {shown!r}).')
    return True


def _fill_date(page, value):
    selector = _field(page, 'date')
    current = page.evaluate(FIELD_VALUE_JS, selector) or ''
    placeholder = page.get_attribute(selector, 'placeholder') or ''
    day = date.fromisoformat(value)
    want = value if (page.get_attribute(selector, 'type') or '').lower() == 'date' else _date_text(day, current, placeholder)
    page.fill(selector, want)
    page.keyboard.press('Enter'); page.keyboard.press('Tab')
    shown = page.evaluate(FIELD_VALUE_JS, selector) or ''
    parsed = resolve_dates([{'date':shown}], order=placeholder_order(placeholder) or (DATE_ORDER.get() if DATE_ORDER.get() != 'AUTO' else None))[0]
    if parsed != day:
        raise ar.AttachError(f'마일리지 거래 날짜가 {day.isoformat()}로 들어가지 않았습니다 (화면: {shown!r}).')


def _pick_vehicle(page, want):
    current, selector = _value(page, 'vehicle')
    if clean(want).casefold() in clean(current).casefold():
        return False
    control = page.locator(selector)
    control.click()
    # Searchable LOVs accept typing; readonly LOVs simply ignore it.
    try:
        input_node = control if control.evaluate("(e) => e.tagName === 'INPUT'") else control.locator('input').first
        if input_node.count():
            if not input_node.get_attribute('readonly'):
                input_node.fill(want)
    except Exception:
        pass
    found = concur_ui.wait_condition(page, OPTION_JS, f"차량 ID 옵션 '{want}'", want, timeout=20000)
    if found == '!AMBIGUOUS':
        raise ar.AttachError(f"차량 ID '{want}' 옵션이 여러 개라 선택하지 않았습니다.")
    page.locator(found).click()
    page.wait_for_timeout(300)
    actual = page.evaluate(FIELD_VALUE_JS, selector) or ''
    if clean(want).casefold() not in clean(actual).casefold():
        raise ar.AttachError(f"차량 ID '{want}' 선택 결과를 확인하지 못했습니다 (화면: {actual!r}).")
    return True


def _numeric_equal(actual, expected, integer=False):
    try:
        a = Decimal(clean(actual).replace(',',''))
        b = Decimal(str(expected))
        return a == b and (not integer or a == a.to_integral_value())
    except (InvalidOperation, ValueError):
        return False


def _expense_id(page, timeout=15000):
    return concur_ui.wait_condition(page, EXPENSE_ID_JS, '새 마일리지 경비 ID', timeout=timeout)


def _open_new(page, report, store, row):
    fp = row_fingerprint(row)
    store.set(row['id'], report=report.url, fingerprint=fp, expense_id=None, state='creating')
    page.goto(report.url, wait_until='domcontentloaded')
    check_context(page, report.url)
    concur_ui.click_target(page, ADD_EXPENSE_JS, '경비 추가')

    # Tenants differ: some show types immediately, some show "Create New
    # Expense" first, and some open a blank detail whose Expense Type LOV must
    # be opened. Use only exact labels and stop on ambiguity.
    option = _maybe_target(page, TYPE_OPTION_JS, list(TYPE_NAMES))
    if not option:
        intermediate = _maybe_target(page, INTERMEDIATE_JS, list(CREATE_NAMES), loops=8)
        if intermediate == '!AMBIGUOUS':
            raise ar.AttachError('새 경비 만들기 메뉴가 여러 개 보여 자동으로 고르지 않았습니다.')
        if intermediate:
            page.locator(intermediate).click()
            option = _maybe_target(page, TYPE_OPTION_JS, list(TYPE_NAMES), loops=15)
    if not option:
        combo = _maybe_target(page, fx.SELECT_TYPE_COMBO_JS, loops=8)
        if combo:
            page.locator(combo).click()
            option = _maybe_target(page, TYPE_OPTION_JS, list(TYPE_NAMES), loops=20)
    if not option:
        raise ar.AttachError('자동차 마일리지 경비 유형을 찾지 못했습니다.'
                             + concur_ui.diagnose(page, '자동차 마일리지 유형 선택'))
    if option == '!AMBIGUOUS':
        raise ar.AttachError('자동차 마일리지 경비 유형이 여러 개 보여 자동으로 고르지 않았습니다.')
    page.locator(option).click()
    expense_id = _expense_id(page)
    store.set(row['id'], report=report.url, fingerprint=fp, expense_id=expense_id, state='bound')
    return expense_id


def _open_bound(page, report_url, expense_id):
    page.goto(ar.expense_url(report_url, expense_id), wait_until='domcontentloaded')
    check_context(page, report_url)
    _field(page, 'date')


def _field_matches(page, row):
    try:
        actual_date, sel = _value(page, 'date')
        placeholder = page.get_attribute(sel, 'placeholder') or ''
        parsed = resolve_dates([{'date':actual_date}], order=placeholder_order(placeholder)
            or (DATE_ORDER.get() if DATE_ORDER.get() != 'AUTO' else None))[0]
        if parsed != date.fromisoformat(row['date']): return False
        for key in ('origin','destination','description'):
            actual, _ = _value(page, key)
            if clean(actual) != clean(row[key]): return False
        actual, _ = _value(page, 'vehicle')
        if clean(row['vehicle']).casefold() not in clean(actual).casefold(): return False
        actual, _ = _value(page, 'distance')
        if not _numeric_equal(actual, row['distance']): return False
        actual, _ = _value(page, 'passengers')
        if not _numeric_equal(actual, row['passengers'], integer=True): return False
        return True
    except Exception:
        return None


def _receipt_status(page, report_url, expense_id):
    page.goto(report_url, wait_until='domcontentloaded')
    check_context(page, report_url)
    rows = ar.rows_when_ready(page, allow_incomplete=True)
    matches = [r for r in rows if r.expense_id == expense_id]
    return matches[0].has_receipt if len(matches) == 1 else None


def _verify(page, report_url, row, expense_id):
    _open_bound(page, report_url, expense_id)
    fields = _field_matches(page, row)
    receipt = _receipt_status(page, report_url, expense_id)
    return fields, receipt


def _apply_fields(page, row):
    _fill_date(page, row['date'])
    _fill(page, 'origin', row['origin'])
    _fill(page, 'destination', row['destination'])
    _pick_vehicle(page, row['vehicle'])
    _fill(page, 'distance', row['distance'])
    _fill(page, 'passengers', row['passengers'])
    _fill(page, 'description', row['description'])


def run(page, report, folder, apply, limit=None):
    path = Path(folder) / BOOK_NAME
    if not path.exists():
        return RunResult(0, '차량 마일리지 0건')
    book = MileageBook(folder)
    rows = list(book.rows)
    if limit:
        rows = rows[:limit]
    if not rows:
        return RunResult(0, '차량 마일리지 0건')
    print(f'차량 마일리지 {len(rows)}건을 ' + ('Concur에 신규 생성합니다.' if apply else '미리보기합니다.'))
    for row in rows:
        book.check_image(row)
        print(f"  {row['date']} · {row['origin']} → {row['destination']} · {row['vehicle']} · {row['distance']}km"
              + (f" · 예상 {row['estimate']}원" if row.get('estimate') else ''))
    if not apply:
        return RunResult(0, f'마일리지 미리보기 {len(rows)}건 · Concur 변경 없음')

    store = BindingStore(folder)
    failures = verified = created = 0
    for number, row in enumerate(rows, 1):
        prefix = f'[마일리지 {number}/{len(rows)}]'
        fp = row_fingerprint(row)
        entry = store.get(row['id'])
        try:
            if entry and (entry.get('report') != report.url or entry.get('fingerprint') != fp):
                raise ar.AttachError('이 로컬 마일리지 행의 이전 Concur 연결 정보와 입력 내용/리포트가 달라 자동 처리하지 않았습니다.')
            expense_id = entry.get('expense_id') if entry else None
            state = entry.get('state') if entry else None

            if state in ('saving','needs_review') and expense_id:
                fields, receipt = _verify(page, report.url, row, expense_id)
                if fields is True and receipt is True:
                    store.set(row['id'], state='verified')
                    print(f'{prefix} 이전 저장 결과 확인 완료: {expense_id}')
                    verified += 1
                    continue
                store.set(row['id'], state='needs_review')
                raise ar.AttachError('이전 신규 생성/저장 결과가 확정되지 않아 자동 재전송하지 않았습니다.')
            if state in ('creating','needs_review') and not expense_id:
                store.set(row['id'], state='needs_review')
                raise ar.AttachError('이전 실행에서 새 경비 ID를 확보하기 전에 생성 결과가 불명확해졌습니다. 중복 생성 방지를 위해 자동 재시도하지 않습니다.')
            if state == 'verified' and expense_id:
                fields, receipt = _verify(page, report.url, row, expense_id)
                if fields is True and receipt is True:
                    print(f'{prefix} 이미 저장·지도 첨부 확인됨: {expense_id}')
                    verified += 1
                    continue
                store.set(row['id'], state='needs_review')
                raise ar.AttachError('기존 완료 기록과 현재 Concur 값이 달라 자동 수정하지 않았습니다.')

            if not expense_id:
                print(f'{prefix} 새 자동차 마일리지 경비 생성')
                expense_id = _open_new(page, report, store, row)
                created += 1
            else:
                _open_bound(page, report.url, expense_id)

            print(f'{prefix} 날짜·경로·차량·거리·탑승자·설명 입력')
            _apply_fields(page, row)

            # Once an upload/save can start, a rerun must verify instead of sending again.
            store.set(row['id'], state='saving')
            map_path = book.check_image(row)
            page.set_input_files(ar.UPLOAD_INPUT, str(map_path))
            page.wait_for_timeout(1500)
            fx._save_expense(page, None, report.url, reopen=False)

            fields, receipt = _verify(page, report.url, row, expense_id)
            if fields is not True or receipt is not True:
                store.set(row['id'], state='needs_review')
                raise ar.AttachError('저장 후 입력값 또는 지도 영수증 첨부를 확인하지 못했습니다.')
            store.set(row['id'], state='verified')
            verified += 1
            print(f'{prefix} 저장 확인 완료: {expense_id}')
        except Exception as exc:
            failures += 1
            current = store.get(row['id'])
            if current and current.get('state') in ('creating','saving'):
                store.set(row['id'], state='needs_review')
            print(f'{prefix} 보류: {exc}')
    summary = f'차량 마일리지 신규 생성 시도 {created}건 · 저장 확인 {verified}건 · 보류 {failures}건'
    print(summary)
    return RunResult(int(bool(failures)), summary)
