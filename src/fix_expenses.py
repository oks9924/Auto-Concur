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
from playwright.sync_api import sync_playwright

from . import console
from .attach_receipts import (
    START_URL,
    WAIT_AMOUNT_JS,
    AttachError,
    Row,
    _eval,
    expense_url,
    read_rows,
)

PROFILE_DIR = Path("browser-profile") / "concur"

# 옵션 id가 '{React가만든id}-list-_-_-{코드}-_-_-{그룹}' 형태다. 앞부분은 매번
# 바뀌므로 코드만 보고 잡는다.
CODE_MEAL = "01182"  # 내부 직원간 식음료 (점심, 야근식대, 부서회식, 음료 등)
CODE_LODGING = "LODNG"  # 숙박비

LABEL_MEAL = "내부 직원간 식음료"
LABEL_LODGING = "숙박비"
LABEL_PERSONAL_MEAL = "개인 식사"

LODGING_THRESHOLD = 100_000

BUSINESS_PURPOSE = "현대 중공업 식대"
COMMENT = "현대 중공업 출장 식대"
ATTENDEE_QUERY = "kyungsik.oh"

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

HAS_ATTENDEE_OPTION_JS = """
() => [...document.querySelectorAll('li[role="option"]')]
  .some(o => !(o.id || '').includes('CREATE_NEW_ATTENDEE'))
"""

SELECT_ATTENDEE_OPTION_JS = (
    "() => {"
    + MARK_FN
    + """
  return mark([...document.querySelectorAll('li[role="option"]')]
    .find(o => !(o.id || '').includes('CREATE_NEW_ATTENDEE')));
}"""
)

# 참석자 버튼은 '참석자 (0)' 처럼 개수를 달고 있다.
ATTENDEE_COUNT_JS = """
() => {
  const b = [...document.querySelectorAll('button')]
    .find(x => /^참석자\\s*\\(\\d+\\)/.test((x.innerText || '').trim()));
  if (!b) return null;
  return parseInt((b.innerText.match(/\\((\\d+)\\)/) || [])[1], 10);
}
"""


@dataclass
class Plan:
    row: Row
    type_code: str | None  # None이면 경비유형은 그대로 둔다
    type_label: str
    fill_meal: bool  # 참석자·목적·코멘트를 채울지

    def summary(self) -> str:
        what = []
        if self.type_code:
            what.append(f"유형 -> {self.type_label}")
        if self.fill_meal:
            what.append("참석자·목적·코멘트")
        return ", ".join(what)


def decide(row: Row) -> Plan | None:
    """이 경비를 어떻게 고칠지. 대상이 아니면 None."""
    kind = row.expense_type or ""
    if row.amount is not None and row.amount >= LODGING_THRESHOLD:
        if LABEL_LODGING in kind:
            return None  # 이미 숙박비다
        return Plan(row, CODE_LODGING, LABEL_LODGING, fill_meal=False)
    if kind.startswith(LABEL_PERSONAL_MEAL):
        return Plan(row, CODE_MEAL, LABEL_MEAL, fill_meal=True)
    if kind.startswith(LABEL_MEAL):
        return Plan(row, None, kind, fill_meal=True)
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


def _add_attendee(page, report_url: str, expense_id: str) -> bool:
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
    page.keyboard.type(ATTENDEE_QUERY, delay=80)

    # 타이핑이 실제로 그 칸에 들어갔는지 먼저 본다. 검색 결과가 안 뜨는 것과
    # 애초에 입력이 안 된 것은 원인이 다르다.
    _wait_js(
        page,
        "(a) => { const el = document.querySelector(a.sel);"
        " return !!el && (el.value || '').includes(a.q); }",
        "검색어가 입력창에 들어가는 것",
        arg={"sel": selector, "q": ATTENDEE_QUERY},
        timeout=10000,
    )
    _wait_js(page, HAS_ATTENDEE_OPTION_JS, f"'{ATTENDEE_QUERY}' 검색 결과", timeout=25000)

    _click_marked(page, SELECT_ATTENDEE_OPTION_JS, f"'{ATTENDEE_QUERY}' 검색 결과")
    page.wait_for_timeout(1500)
    page.get_by_role("button", name="저장").first.click()

    # 저장이 끝나 모달이 닫히는 것을 확인한다.
    _wait_js(
        page,
        "() => !document.querySelector('li[role=\"option\"]')"
        " && !location.search.includes('modal=attendees')",
        "참석자 저장 완료",
        timeout=30000,
    )
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

    if plan.fill_meal:
        if _fill_if_empty(page, PURPOSE_FIELD, BUSINESS_PURPOSE, "비즈니스 목적"):
            done.append("목적")
        if _fill_if_empty(page, COMMENT_FIELD, COMMENT, "코멘트"):
            done.append("코멘트")

    if done:
        page.get_by_role("button", name="경비 저장").first.click()
        # 저장이 끝나고 화면이 다시 그려질 때까지 기다린다. 여기서 서둘러
        # 참석자 모달로 넘어가면 방금 넣은 값이 날아간다.
        page.wait_for_timeout(2000)
        _wait_js(page, WAIT_AMOUNT_JS, "저장 후 화면", arg=str(row.amount))

    if plan.fill_meal and _add_attendee(page, report_url, row.expense_id):
        done.append("참석자")

    return ", ".join(done) if done else "이미 되어 있음"


def run(apply: bool, limit: int | None) -> int:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=False
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(START_URL, wait_until="domcontentloaded")

        print("\n" + "=" * 64)
        print("  Concur에 로그인하고 처리할 경비 리포트를 열어라.")
        print("  경비 목록이 보이는 상태에서 Enter를 눌러라.")
        print("=" * 64)
        console.wait_enter("리포트를 열었으면 Enter > ")

        page.wait_for_timeout(2000)
        report_url = page.url
        rows = read_rows(page)
        plans = [p for p in (decide(r) for r in rows if r.expense_id) if p]

        print(f"\n경비 {len(rows)}건 중 손댈 것 {len(plans)}건\n")
        for plan in plans:
            r = plan.row
            print(f"  {r.when} {r.amount:>9,}원  {r.expense_type[:22]:22} -> {plan.summary()}")

        skipped = len(rows) - len(plans)
        if skipped:
            print(f"\n건드리지 않음 {skipped}건 (이미 맞거나 대상 아님)")

        if not apply:
            print("\n계획만 출력했다. 실제로 고치려면 --apply 를 붙여라.")
            print("처음에는 --apply --limit 1 로 한 건만 해보고 Concur에서 확인해라.")
            ctx.close()
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

        ctx.close()

    print(f"\n{done}건 처리")
    if failed:
        print(f"실패 {len(failed)}건:")
        for plan, why in failed:
            print(f"  ! {plan.row.when} {plan.row.amount:,}원: {why}")
        return 1
    return 0


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="Concur 경비유형·참석자·목적·코멘트 채우기")
    ap.add_argument("--apply", action="store_true", help="실제로 고친다")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 (동작 확인용)")
    args = ap.parse_args()
    try:
        return run(args.apply, args.limit)
    except AttachError as exc:
        print(f"\n중단: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
