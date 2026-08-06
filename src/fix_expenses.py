"""Concur 경비의 경비유형·참석자·목적·코멘트를 채운다.

로그인(SSO)과 리포트 열기는 사람이 한다. 그 다음 목록을 읽어 규칙대로 고친다.

    python -m src.fix_expenses                     # 계획만 (아무것도 안 바꿈)
    python -m src.fix_expenses --apply --limit 1   # 한 건만 해보고 확인
    python -m src.fix_expenses --apply             # 나머지 전부

규칙:
  금액 >= 100,000        -> 숙박비. 참석자·목적·코멘트는 넣지 않는다.
  개인 식사 (SISW Only)  -> 내부 직원간 식음료 + 참석자·목적·코멘트
  내부 직원간 식음료      -> 참석자·목적·코멘트만 채운다
  그 외(환급 불가 등)     -> 건드리지 않는다

이미 채워진 값은 덮어쓰지 않는다. 참석자가 이미 있으면 추가하지 않는다.
그래서 두 번 돌려도 안전하고, 중간에 끊겨도 이어서 하면 된다.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from . import console, settings, sheet
from .attach_receipts import match as match_rows
from .attach_receipts import open_report
from .attach_receipts import (
    WAIT_AMOUNT_JS,
    AttachError,
    Row,
    _eval,
    expense_url,
    read_rows,
)

PROFILE_DIR = Path("browser-profile") / "concur"

LABEL_MEAL = "내부 직원간 식음료"
LABEL_PERSONAL_MEAL = "개인 식사"

PURPOSE_FIELD = "#businessPurpose"
COMMENT_FIELD = "textarea#comment"

# 라벨로 콤보박스를 찾는다. id는 React가 매번 새로 만들어서 못 쓴다.
#
# 라벨을 찾는 방법이 여럿이고 요소마다 다르다. inspect_page가 fields.json을
# 만들 때 쓴 것과 똑같은 순서로 찾아야 한다. 감싼 form-field만 보다가
# aria-label에 있는 라벨을 놓쳐서 참석자 검색창을 못 찾았다.
FIND_COMBO_FN = """
  const labelOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\\s+/)
        .map(id => (document.getElementById(id) || {}).innerText || '')
        .join(' ').trim();
      if (t) return t;
    }
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return (l.innerText || '').trim();
    }
    const own = el.closest('label');
    if (own) return (own.innerText || '').trim();
    const wrap = el.closest('[class*="form-field"], [class*="form-group"]');
    if (wrap) {
      const l = wrap.querySelector('label');
      if (l) return (l.innerText || '').trim();
    }
    return '';
  };
  const findCombo = (re) => [...document.querySelectorAll('[role="combobox"]')]
    .find(c => re.test(labelOf(c)));
"""

# 못 찾았을 때 화면의 콤보박스를 전부 남긴다. 추측 대신 근거로 고치기 위해서다.
DUMP_COMBOS_JS = (
    "() => {"
    + FIND_COMBO_FN
    + """
  return [...document.querySelectorAll('[role="combobox"]')].map(c => ({
    label: labelOf(c),
    ariaLabel: c.getAttribute('aria-label'),
    id: c.id || null,
    cls: (typeof c.className === 'string' ? c.className : '').slice(0, 90),
    text: (c.innerText || '').trim().slice(0, 60),
    hasInput: !!c.querySelector('input'),
    visible: c.offsetParent !== null,
  }));
}"""
)

# 요소에 표시를 남기고 셀렉터를 돌려준다. 실제 클릭은 Playwright가 한다.
#
# JS의 element.click()은 click 이벤트 하나만 쏜다. React 드롭다운은 보통
# onMouseDown/onPointerDown을 들어서 반응하지 않는다. 참석자 콤보박스가
# 눌리지 않은 이유였다.
MARK_FN = """
  const mark = (el) => {
    if (!el) return null;
    document.querySelectorAll('[data-auto-target]')
      .forEach(e => e.removeAttribute('data-auto-target'));
    el.setAttribute('data-auto-target', '1');
    return '[data-auto-target="1"]';
  };
"""

TYPE_COMBO_READY_JS = "() => {" + FIND_COMBO_FN + " return !!findCombo(/Expense Type|경비 유형/); }"

SELECT_TYPE_COMBO_JS = (
    "() => {" + FIND_COMBO_FN + MARK_FN + " return mark(findCombo(/Expense Type|경비 유형/)); }"
)

HAS_TYPE_OPTION_JS = """
(code) => [...document.querySelectorAll('li[role="option"]')]
  .some(o => (o.id || '').includes('-_-_-' + code + '-_-_-'))
"""

SELECT_TYPE_OPTION_JS = (
    "(code) => {"
    + MARK_FN
    + """
  return mark([...document.querySelectorAll('li[role="option"]')]
    .find(o => (o.id || '').includes('-_-_-' + code + '-_-_-')));
}"""
)

# 참석자 모달. 콤보박스(텍스트 '참석자 추가')를 눌러야 입력창이 생긴다.
# 입력창은 콤보박스의 자식이 아니라 같은 form-field 안의 형제다.
ATTENDEE_COMBO_READY_JS = (
    "() => {" + FIND_COMBO_FN + " return !!findCombo(/이름 또는 기업 이메일/); }"
)

SELECT_ATTENDEE_COMBO_JS = (
    "() => {" + FIND_COMBO_FN + MARK_FN + " return mark(findCombo(/이름 또는 기업 이메일/)); }"
)

# 상세 폼의 입력들. 위로 올라가다 이것들을 잘못 집으면 엉뚱한 데 타이핑한다.
DETAIL_FIELD_IDS = "businessPurpose,comment,vendorName,transactionAmount,taxTransactionAmount1,upload-file"

# 참석자 검색창을 화면 전체에서 찾는다. 콤보박스에서 거슬러 올라가는 방식은
# 이 구조와 맞지 않았다(화면에는 떠 있는데 못 찾았다).
#
# 구분 근거: 상세 폼의 입력은 전부 name을 갖는다(businessPurpose, vendorName,
# transactionAmount, paymentType, transactionCurrencyName...). 참석자 검색창만
# name이 없다. 거래일 입력도 name이 없어서 id로 따로 뺀다.
ATTENDEE_INPUT_JS = """
(ignoreCsv) => {
  const ignore = new Set(ignoreCsv.split(','));
  const el = [...document.querySelectorAll('input')].find(x => {
    const type = (x.getAttribute('type') || 'text').toLowerCase();
    if (!['text', 'search', 'email'].includes(type)) return false;
    if (x.getAttribute('name')) return false;
    const id = x.id || '';
    if (ignore.has(id) || id.startsWith('transactionDate')) return false;
    if (id.endsWith('-select-input')) return false;
    const r = x.getBoundingClientRect();  // offsetParent는 position:fixed에서 null이다
    return r.width > 0 && r.height > 0;
  });
  if (!el) return null;
  if (!el.id) el.id = 'auto-concur-attendee-input';
  return '#' + CSS.escape(el.id);
}
"""

# 못 찾았을 때 화면의 입력들을 남긴다.
DUMP_INPUTS_JS = """
() => [...document.querySelectorAll('input, textarea')].map(x => {
  const r = x.getBoundingClientRect();
  const chain = [];
  let n = x.parentElement;
  for (let i = 0; i < 5 && n; i++) {
    const c = typeof n.className === 'string' ? n.className.slice(0, 50) : '';
    chain.push(n.tagName.toLowerCase() + (c ? '.' + c : ''));
    n = n.parentElement;
  }
  return {
    tag: x.tagName.toLowerCase(),
    type: x.getAttribute('type'),
    id: x.id || null,
    name: x.getAttribute('name'),
    ariaLabel: x.getAttribute('aria-label'),
    placeholder: x.getAttribute('placeholder'),
    value: (x.value || '').slice(0, 40),
    size: `${Math.round(r.width)}x${Math.round(r.height)}`,
    ancestors: chain,
  };
})
"""

# 검색이 비동기라 결과가 오기 전에 '결과 없음' 자리표시자가 먼저 뜬다.
# 그걸 진짜 결과로 보고 누르면 aria-disabled 항목을 누르려다 실패한다.
REAL_ATTENDEE_FN = """
  const isRealAttendee = (o) => {
    const id = o.id || '';
    if (id.includes('CREATE_NEW_ATTENDEE') || id.includes('NO_RESULTS')) return false;
    if (o.getAttribute('aria-disabled') === 'true') return false;
    const cls = typeof o.className === 'string' ? o.className : '';
    return !cls.includes('--disabled') && !cls.includes('--no-results');
  };
"""

HAS_ATTENDEE_OPTION_JS = (
    "() => {"
    + REAL_ATTENDEE_FN
    + """
  return [...document.querySelectorAll('li[role="option"]')].some(isRealAttendee);
}"""
)

SELECT_ATTENDEE_OPTION_JS = (
    "() => {"
    + MARK_FN
    + REAL_ATTENDEE_FN
    + """
  return mark([...document.querySelectorAll('li[role="option"]')].find(isRealAttendee));
}"""
)

# 참석자 버튼은 '참석자 (0)' 처럼 개수를 달고 있다.
ATTENDEE_COUNT_JS_BODY = """
  const b = [...document.querySelectorAll('button')]
    .find(x => /^참석자\\s*\\(\\d+\\)/.test((x.innerText || '').trim()));
  const n = b ? parseInt((b.innerText.match(/\\((\\d+)\\)/) || [])[1], 10) : null;
"""

ATTENDEE_COUNT_JS = "() => {" + ATTENDEE_COUNT_JS_BODY + " return n; }"

# 새 경비유형(택시 등)이 생겼을 때 코드를 알아내려고 쓴다.
DUMP_TYPES_JS = """
() => {
  const seen = new Map();
  for (const o of document.querySelectorAll('li[role="option"]')) {
    const m = (o.id || '').match(/-_-_-(.+?)-_-_-/);
    if (m && !seen.has(m[1])) seen.set(m[1], (o.innerText || '').trim());
  }
  return [...seen].map(([code, label]) => ({ code, label }));
}
"""


@dataclass
class Plan:
    row: Row
    type_code: str | None  # None이면 경비유형은 그대로 둔다
    type_label: str
    purpose: str = ""
    comment: str = ""
    attendee: str = ""

    @property
    def fill_meal(self) -> bool:
        return bool(self.purpose or self.comment or self.attendee)

    def summary(self) -> str:
        what = []
        if self.type_code:
            what.append(f"유형 -> {self.type_label}")
        filled = [n for n, v in (("목적", self.purpose), ("코멘트", self.comment),
                                 ("참석자", self.attendee)) if v]
        if filled:
            what.append("·".join(filled))
        return ", ".join(what) or "변경 없음"


@dataclass
class Rules:
    """규칙에 쓰는 값들. 규칙 자체는 코드에 둔다."""

    threshold: int
    large_code: str
    large_label: str
    meal_code: str
    purpose: str
    comment: str
    attendee: str


def rules_from(cfg: dict) -> Rules:
    large = cfg["large_amount_type"]
    return Rules(
        threshold=int(cfg["lodging_threshold"]),
        large_code=settings.code_for(cfg, large),
        large_label=large,
        meal_code=settings.code_for(cfg, LABEL_MEAL),
        purpose=cfg["business_purpose"],
        comment=cfg["comment"],
        attendee=cfg["attendee_query"],
    )


def decide(row: Row, rules: Rules) -> Plan | None:
    """이 경비를 어떻게 고칠지. 대상이 아니면 None."""
    kind = row.expense_type or ""
    if row.amount is not None and row.amount >= rules.threshold:
        if rules.large_label in kind:
            return None  # 이미 그 유형이다
        return Plan(row, rules.large_code, rules.large_label)
    if kind.startswith(LABEL_PERSONAL_MEAL):
        return Plan(row, rules.meal_code, LABEL_MEAL, rules.purpose, rules.comment, rules.attendee)
    if kind.startswith(LABEL_MEAL):
        return Plan(row, None, kind, rules.purpose, rules.comment, rules.attendee)
    return None


def _dump(page, tag: str, script: str) -> str | None:
    """화면 상태를 남긴다. 추측 대신 근거로 고치기 위해서다."""
    try:
        data = _eval(page, script)
    except Exception:
        return None
    out = Path("inspect-out")
    out.mkdir(exist_ok=True)
    path = out / f"{tag}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _wait_js(page, script: str, what: str, arg=None, timeout: int = 30000) -> None:
    """조건이 참이 될 때까지 기다린다.

    고정 시간으로 기다리면 안 된다. Concur는 상세 폼을 나눠서 그리고, 모달은
    주소로 열면 앱 전체를 다시 띄운다. 얼마나 걸릴지는 그때그때 다르다.
    """
    try:
        if arg is None:
            page.wait_for_function(script, timeout=timeout)
        else:
            page.wait_for_function(script, arg=arg, timeout=timeout)
    except PWTimeout:
        raise AttachError(f"{what}을(를) 기다렸지만 나타나지 않았다") from None


def _click_marked(page, script: str, what: str, arg=None) -> None:
    """JS로 대상을 표시하고 실제 마우스로 누른다."""
    selector = _eval(page, script) if arg is None else _eval(page, script, arg)
    if not selector:
        raise AttachError(f"{what}을(를) 찾지 못했다")
    page.click(selector, timeout=15000)


def _set_type(page, code: str, label: str) -> None:
    try:
        _wait_js(page, TYPE_COMBO_READY_JS, "경비 유형 콤보박스", timeout=25000)
    except AttachError as exc:
        dump = _dump(page, "combos-expense-type", DUMP_COMBOS_JS)
        raise AttachError(f"{exc}" + (f" (화면의 콤보박스 목록: {dump})" if dump else "")) from None
    _click_marked(page, SELECT_TYPE_COMBO_JS, "경비 유형 콤보박스")

    _wait_js(page, HAS_TYPE_OPTION_JS, f"경비 유형 옵션({code})", arg=code, timeout=20000)
    _click_marked(page, SELECT_TYPE_OPTION_JS, f"경비 유형 옵션({code})", arg=code)

    # 고른 값이 실제로 반영됐는지 본다. 눌렀다고 바뀐 것은 아니다.
    _wait_js(
        page,
        "(want) => {"
        + FIND_COMBO_FN
        + " const cb = findCombo(/Expense Type|경비 유형/);"
        " return !!cb && (cb.innerText || '').includes(want); }",
        f"경비 유형이 '{label}'로 바뀌는 것",
        arg=label,
        timeout=20000,
    )


def _fill_if_empty(page, selector: str, value: str, what: str) -> bool:
    """비어 있을 때만 채운다. 사람이 써둔 것을 덮어쓰지 않는다."""
    try:
        page.wait_for_selector(selector, timeout=20000)
    except PWTimeout:
        # 조용히 넘기면 안 된다. 안 채워졌는데 채운 줄 알게 된다.
        raise AttachError(f"{what} 필드를 찾지 못했다") from None
    if page.input_value(selector).strip():
        return False
    page.fill(selector, value)
    return True


def _add_attendee(page, report_url: str, expense_id: str, query: str) -> bool:
    """참석자가 없을 때만 추가한다. 이미 있으면 건드리지 않는다."""
    count = _eval(page, ATTENDEE_COUNT_JS)
    if count is None:
        raise AttachError("참석자 버튼을 찾지 못했다")
    if count > 0:
        return False

    # 주소로 모달을 열면 앱 전체를 다시 띄운다. 넉넉히 기다려야 한다.
    page.goto(
        f"{expense_url(report_url, expense_id)}?modal=attendees&context=entry",
        wait_until="domcontentloaded",
    )
    try:
        _wait_js(page, ATTENDEE_COMBO_READY_JS, "참석자 검색 콤보박스", timeout=30000)
        # 눌러야 입력창이 생긴다. 콤보박스 자체에는 input이 없다.
        # 실제 마우스로 눌러야 한다. JS click은 React가 안 듣는다.
        _click_marked(page, SELECT_ATTENDEE_COMBO_JS, "참석자 검색 콤보박스")
        _wait_js(
            page, ATTENDEE_INPUT_JS, "참석자 검색 입력창", arg=DETAIL_FIELD_IDS, timeout=15000
        )
    except AttachError as exc:
        _dump(page, "combos-attendee", DUMP_COMBOS_JS)
        inputs = _dump(page, "inputs-attendee", DUMP_INPUTS_JS)
        raise AttachError(f"{exc}" + (f" (화면의 입력 목록: {inputs})" if inputs else "")) from None
    selector = _eval(page, ATTENDEE_INPUT_JS, DETAIL_FIELD_IDS)

    # fill 대신 실제 타이핑. 자동완성은 키 입력을 보고 검색을 띄운다.
    page.click(selector)
    page.keyboard.type(query, delay=80)

    # 타이핑이 실제로 그 칸에 들어갔는지 먼저 본다. 검색 결과가 안 뜨는 것과
    # 애초에 입력이 안 된 것은 원인이 다르다.
    _wait_js(
        page,
        "(a) => { const el = document.querySelector(a.sel);"
        " return !!el && (el.value || '').includes(a.q); }",
        "검색어가 입력창에 들어가는 것",
        arg={"sel": selector, "q": query},
        timeout=10000,
    )
    _wait_js(page, HAS_ATTENDEE_OPTION_JS, f"'{query}' 검색 결과", timeout=25000)

    _click_marked(page, SELECT_ATTENDEE_OPTION_JS, f"'{query}' 검색 결과")
    page.wait_for_timeout(1500)

    # exact=True 가 중요하다. 기본은 부분 일치라 '저장'이 '경비 저장'에도
    # 걸려서 모달 버튼 대신 뒤쪽 버튼을 누를 수 있다.
    page.get_by_role("button", name="저장", exact=True).first.click()

    # 참석자 수가 늘었는지로 확인한다. 모달이 닫혔는지나 주소가 바뀌었는지는
    # 추측이었고, 원래 확인하려던 것은 참석자가 실제로 붙었는지다.
    try:
        _wait_js(page, "() => {" + ATTENDEE_COUNT_JS_BODY + " return n !== null && n > 0; }",
                 "참석자가 추가되는 것", timeout=30000)
    except AttachError:
        _dump(page, "inputs-attendee-save", DUMP_INPUTS_JS)
        raise
    return True


def apply_plan(page, plan: Plan, report_url: str) -> str:
    row = plan.row
    page.goto(expense_url(report_url, row.expense_id), wait_until="domcontentloaded")

    # 이 금액이 화면에 뜰 때까지 기다린다. 대기와 '맞는 경비를 열었나' 확인이
    # 한 번에 된다. 목록과 상세가 같은 화면에 있어서 필드 존재만으로는 모른다.
    _wait_js(page, WAIT_AMOUNT_JS, f"{row.amount:,}원 경비 상세", arg=str(row.amount))

    done = []
    if plan.type_code:
        _set_type(page, plan.type_code, plan.type_label)
        done.append(f"유형->{plan.type_label}")

    if plan.purpose and _fill_if_empty(page, PURPOSE_FIELD, plan.purpose, "비즈니스 목적"):
        done.append("목적")
    if plan.comment and _fill_if_empty(page, COMMENT_FIELD, plan.comment, "코멘트"):
        done.append("코멘트")

    if done:
        page.get_by_role("button", name="경비 저장", exact=True).first.click()
        # 저장이 끝나고 화면이 다시 그려질 때까지 기다린다. 여기서 서둘러
        # 참석자 모달로 넘어가면 방금 넣은 값이 날아간다.
        page.wait_for_timeout(2000)
        _wait_js(page, WAIT_AMOUNT_JS, "저장 후 화면", arg=str(row.amount))

    if plan.attendee and _add_attendee(page, report_url, row.expense_id, plan.attendee):
        done.append("참석자")

    return ", ".join(done) if done else "이미 되어 있음"


def plans_from_sheet(cfg: dict, rows: list[Row], sheet_path: Path, tolerance: int):
    """작업지에 적힌 대로 계획을 만든다. 규칙 대신 사람이 정한 값을 쓴다."""
    entries = sheet.load(sheet_path)
    pairs, missing = match_rows(entries, rows, tolerance)
    plans = []
    for entry, row, how in pairs:
        code, label = None, row.expense_type
        if entry.type_name and entry.type_name not in (row.expense_type or ""):
            code, label = settings.code_for(cfg, entry.type_name), entry.type_name
        plan = Plan(row, code, label, entry.purpose, entry.comment, entry.attendee)
        if plan.type_code or plan.fill_meal:
            plans.append((plan, how))
    return plans, missing


def fix_phase(page, report_url: str, cfg: dict, apply: bool,
              limit: int | None, sheet_path: Path | None) -> int:
    """열려 있는 리포트의 유형·목적·코멘트·참석자를 채운다."""
    rules = rules_from(cfg)
    rows = read_rows(page)

    if sheet_path:
        paired, missing = plans_from_sheet(cfg, [r for r in rows if r.expense_id],
                                           sheet_path, int(cfg["date_tolerance_days"]))
        plans = [p for p, _ in paired]
        if missing:
            print(f"\n작업지에 있으나 Concur에서 못 찾은 것 {len(missing)}건:")
            for entry, why in missing:
                print(f"  {entry.when} {entry.amount:>9,}원  {entry.merchant[:16]} - {why}")
    else:
        plans = [p for p in (decide(r, rules) for r in rows if r.expense_id) if p]

    print(f"\n경비 {len(rows)}건 중 손댈 것 {len(plans)}건\n")
    for plan in plans:
        r = plan.row
        print(f"  {r.when} {r.amount:>9,}원  {r.expense_type[:22]:22} -> {plan.summary()}")

    skipped = len(rows) - len(plans)
    if skipped:
        print(f"\n건드리지 않음 {skipped}건 (이미 맞거나 대상 아님)")

    if not apply:
        print("\n계획만 출력했다. 실제로 고치려면 --apply 를 붙여라.")
        return 0

    if limit:
        plans = plans[:limit]
        print(f"\n--limit {limit} 이므로 {len(plans)}건만 고친다.")

    done, failed = 0, []
    for i, plan in enumerate(plans, 1):
        try:
            what = apply_plan(page, plan, report_url)
            done += 1
            print(f"  [{i}/{len(plans)}] {plan.row.when} {plan.row.amount:,}원 - {what}")
        except (AttachError, PWTimeout) as exc:
            failed.append((plan, str(exc)))
            print(f"  [{i}/{len(plans)}] 실패 {plan.row.when} {plan.row.amount:,}원: {exc}")

    print(f"\n{done}건 처리")
    if failed:
        print(f"실패 {len(failed)}건:")
        for plan, why in failed:
            print(f"  ! {plan.row.when} {plan.row.amount:,}원: {why}")
        return 1
    return 0


def list_types_phase(page, report_url: str) -> int:
    usable = [r for r in read_rows(page) if r.expense_id]
    if not usable:
        raise AttachError("경비가 하나도 없다. 리포트를 열고 다시 해라.")
    page.goto(expense_url(report_url, usable[0].expense_id), wait_until="domcontentloaded")
    _wait_js(page, TYPE_COMBO_READY_JS, "경비 유형 콤보박스", timeout=25000)
    _click_marked(page, SELECT_TYPE_COMBO_JS, "경비 유형 콤보박스")
    _wait_js(page, "() => !!document.querySelector('li[role=\"option\"]')", "경비 유형 목록")
    types = _eval(page, DUMP_TYPES_JS)
    print(f"\n경비유형 {len(types)}개. settings.json 의 expense_type_codes 에 넣어 쓴다.\n")
    for t in sorted(types, key=lambda x: x["label"]):
        print(f'  "{t["label"]}": "{t["code"]}",')
    return 0


def run(apply: bool, limit: int | None, list_types: bool = False,
        sheet_path: Path | None = None) -> int:
    cfg = settings.load()
    pw, ctx, page, report_url = open_report()
    try:
        if list_types:
            return list_types_phase(page, report_url)
        return fix_phase(page, report_url, cfg, apply, limit, sheet_path)
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="Concur 경비유형·참석자·목적·코멘트 채우기")
    ap.add_argument("--apply", action="store_true", help="실제로 고친다")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 (동작 확인용)")
    ap.add_argument("--sheet", nargs="?", const="", type=str,
                    help="작업지(csv/xlsx)대로 넣는다. 값 없이 주면 전표 폴더의 manifest.csv")
    ap.add_argument("--list-types", action="store_true",
                    help="화면의 경비유형과 코드를 뽑는다 (새 유형이 생겼을 때)")
    args = ap.parse_args()
    try:
        path = None
        if args.sheet is not None:
            path = Path(args.sheet) if args.sheet else Path(settings.load()["downloads_dir"]) / "manifest.csv"
        return run(args.apply, args.limit, args.list_types, path)
    except (AttachError, sheet.SheetError) as exc:
        print(f"\n중단: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
