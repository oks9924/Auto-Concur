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

작업지가 유일한 기준이다. 다시 돌리면 유형·목적·코멘트는 적힌 값으로 다시
맞추고(이미 같으면 건드리지 않는다), 참석자는 화면과 견줘서 빠진 사람만 넣는다.
작업지에 없는 참석자가 올라와 있으면 멈추고 알린다 - 지우는 화면은 아직 모른다.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from . import console, settings, sheet
from .sheet import nightly_split
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

# --- 숙박비 -----------------------------------------------------------------
#
# 숙박위치와 Booking Channel은 라벨이 확정적이지 않아서 정규식을 밖에서 준다.
# 경비유형 콤보박스처럼 이름을 코드에 박지 않고 찾는 방식만 공유한다.
COMBO_READY_JS = "(src) => {" + FIND_COMBO_FN + " return !!findCombo(new RegExp(src)); }"

SELECT_COMBO_JS = (
    "(src) => {" + FIND_COMBO_FN + MARK_FN + " return mark(findCombo(new RegExp(src))); }"
)

# 옵션은 보이는 글자로 고른다. 경비유형과 달리 id에 코드가 없다.
OPTION_FN = """
  const findOption = (want) => {
    const opts = [...document.querySelectorAll('li[role="option"]')]
      .filter(o => o.getAttribute('aria-disabled') !== 'true');
    const text = (o) => (o.innerText || '').trim();
    return opts.find(o => text(o) === want) || opts.find(o => text(o).startsWith(want));
  };
"""

HAS_OPTION_JS = "(want) => {" + OPTION_FN + " return !!findOption(want); }"

SELECT_OPTION_JS = "(want) => {" + OPTION_FN + MARK_FN + " return mark(findOption(want)); }"

# 열려 있는 드롭다운의 값들을 그대로 읽는다. 엑셀 드롭다운 목록을 만들 때 쓴다.
READ_OPTIONS_JS = """
() => [...document.querySelectorAll('li[role="option"]')]
  .filter(o => o.getAttribute('aria-disabled') !== 'true')
  .map(o => (o.innerText || '').trim())
  .filter(Boolean)
"""

# 날짜 입력은 달력 위젯이 붙어 있지만 입력창 자체는 타이핑을 받는다.
# id가 '...-date-input-field-input' 꼴이라 그것으로 후보를 좁히고 라벨로 고른다.
FIND_DATE_INPUT_JS = (
    "(src) => {"
    + FIND_COMBO_FN
    + """
  const re = new RegExp(src);
  const el = [...document.querySelectorAll('input')].filter(x => {
    const r = x.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && /date/i.test(x.id || '');
  }).find(x => re.test(labelOf(x)));
  if (!el) return null;
  if (!el.id) el.id = 'auto-concur-date-input';
  return '#' + CSS.escape(el.id);
}"""
)

DUMP_DATE_INPUTS_JS = (
    "() => {"
    + FIND_COMBO_FN
    + """
  return [...document.querySelectorAll('input')].map(x => ({
    id: x.id || null, name: x.getAttribute('name'), label: labelOf(x),
    value: (x.value || '').slice(0, 30),
  }));
}"""
)

# 탭이나 버튼을 글자로 찾는다. 가장 안쪽 것을 고른다 - 바깥 컨테이너에도
# 같은 글자가 들어 있어서 그걸 누르면 아무 일도 안 난다.
FIND_BY_TEXT_JS = (
    "(want) => {"
    + MARK_FN
    + """
  const hit = [...document.querySelectorAll(
    '[role="tab"], button, a, li, span, div, label'
  )].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (e.innerText || '').trim() === want;
  });
  if (!hit.length) return null;
  hit.sort((a, b) => a.getElementsByTagName('*').length - b.getElementsByTagName('*').length);
  return mark(hit[0]);
}"""
)

HAS_TEXT_JS = """
(want) => [...document.querySelectorAll('[role="tab"], button, a, li, span, div, label')]
  .some(e => {
    const r = e.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (e.innerText || '').trim() === want;
  })
"""

# 항목별 명세 표. 행마다 날짜와 금액 입력이 있고, 첫 금액 입력이 객실 요금이다.
# 칼럼 순서를 짐작하지 않고 행 안의 입력을 순서대로 돌려준다.
ITEMIZATION_ROWS_JS = """
() => {
  const rows = [...document.querySelectorAll('[role="row"], tbody tr')].filter(r => {
    const box = r.getBoundingClientRect();
    return box.width > 0 && r.querySelector('input');
  });
  return rows.map((r, i) => {
    const inputs = [...r.querySelectorAll('input')].filter(x => {
      const b = x.getBoundingClientRect();
      return b.width > 0 && b.height > 0 && x.type !== 'checkbox' && x.type !== 'radio';
    });
    inputs.forEach((x, j) => {
      if (!x.id) x.id = 'auto-concur-item-' + i + '-' + j;
    });
    return {
      row: i,
      text: (r.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
      inputs: inputs.map(x => ({
        selector: '#' + CSS.escape(x.id),
        name: x.getAttribute('name'),
        value: (x.value || '').slice(0, 20),
      })),
    };
  });
}
"""

LABEL_LODGING = "숙박비"
TAB_ITEMIZATION = "항목별 명세"
RECUR_DIFFERENT_DAILY = "일일 금액 다름"

# 라벨 정규식. 화면 문구가 조금 달라도 걸리게 넉넉히 잡는다.
RE_CHECKIN = "체크인|입실|Check.?in"
RE_CHECKOUT = "체크아웃|퇴실|Check.?out"
RE_LOCATION = "숙박 ?위치|숙박지|Lodging Location|Location"
RE_CHANNEL = "Booking Channel|예약 ?채널"
RE_RECURRENCE = "반복|Recurrence"

# 모달에 올라와 있는 참석자 이름. 표 행의 첫 칸이 이름이다. 행 전체 텍스트에는
# 금액·인원 같은 것이 섞여 있어서 첫 줄만 본다.
ATTENDEE_NAMES_JS = """
() => {
  const rows = [...document.querySelectorAll(
    '[role="row"][data-testid="data-row"], table tbody tr'
  )];
  return rows
    .map(r => {
      const cell = r.querySelector('td, [role="cell"], [role="gridcell"]') || r;
      return (cell.innerText || '').trim().split('\\n')[0].trim();
    })
    .filter(Boolean);
}
"""

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


def _md(when: date) -> str:
    """8/2 처럼 보여준다. strftime('%-m/%-d')는 Windows에서 안 된다."""
    return f"{when.month}/{when.day}"


@dataclass
class Lodging:
    """숙박비 상세에 넣을 값. 입실·퇴실이 있어야 성립한다."""

    checkin: date
    checkout: date
    location: str
    channel: str

    @property
    def nights(self) -> int:
        return (self.checkout - self.checkin).days

    def dates(self) -> list[date]:
        """항목별 명세 표에 들어갈 날짜들. 입실일부터 숙박일수만큼."""
        return [self.checkin + timedelta(days=i) for i in range(self.nights)]


@dataclass
class Plan:
    row: Row
    type_code: str | None  # None이면 경비유형은 그대로 둔다
    type_label: str
    purpose: str = ""
    comment: str = ""
    attendee: str = ""
    lodging: Lodging | None = None

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
        if self.lodging:
            per = nightly_split(self.row.amount or 0, self.lodging.nights)
            what.append(
                f"숙박 {self.lodging.nights}박 "
                f"({_md(self.lodging.checkin)}~{_md(self.lodging.checkout)}), "
                f"일일 {per[0]:,}원"
            )
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
        raise AttachError(f"{what}이(가) 나타나지 않았습니다") from None


def _click_marked(page, script: str, what: str, arg=None) -> None:
    """JS로 대상을 표시하고 실제 마우스로 누른다."""
    selector = _eval(page, script) if arg is None else _eval(page, script, arg)
    if not selector:
        raise AttachError(f"{what}을(를) 찾지 못했습니다")
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


def _set_field(page, selector: str, value: str, what: str) -> bool:
    """작업지에 적힌 값으로 맞춘다. 이미 다른 값이 있으면 덮어쓴다.

    작업지가 유일한 기준이라 화면에 뭐가 적혀 있든 그쪽으로 맞춘다. 이미
    같은 값이면 건드리지 않는다 - 저장 한 번을 아낀다.
    """
    try:
        page.wait_for_selector(selector, timeout=20000)
    except PWTimeout:
        # 조용히 넘기면 안 된다. 안 채워졌는데 채운 줄 알게 된다.
        raise AttachError(f"{what} 입력칸을 찾지 못했습니다") from None
    if page.input_value(selector).strip() == value.strip():
        return False
    page.fill(selector, value)
    return True


def _pick_from_combo(page, label_re: str, want: str, what: str) -> None:
    """라벨로 콤보박스를 찾아 열고, 보이는 글자로 옵션을 고른다."""
    try:
        _wait_js(page, COMBO_READY_JS, f"{what} 콤보박스", arg=label_re, timeout=25000)
    except AttachError:
        dump = _dump(page, f"combos-{what}", DUMP_COMBOS_JS)
        raise AttachError(
            f"{what} 콤보박스를 찾지 못했습니다" + (f" (화면의 콤보박스 목록: {dump})" if dump else "")
        ) from None
    _click_marked(page, SELECT_COMBO_JS, f"{what} 콤보박스", arg=label_re)

    try:
        _wait_js(page, HAS_OPTION_JS, f"{what} 옵션 '{want}'", arg=want, timeout=20000)
    except AttachError:
        dump = _dump(page, f"options-{what}", READ_OPTIONS_JS)
        raise AttachError(
            f"{what} 목록에 '{want}' 가 없습니다"
            + (f" (화면의 목록: {dump})" if dump else "")
            + ". 엑셀 드롭다운 목록을 --list-lodging 으로 다시 뽑아 주세요."
        ) from None
    _click_marked(page, SELECT_OPTION_JS, f"{what} 옵션 '{want}'", arg=want)
    page.wait_for_timeout(800)


def _type_date(page, label_re: str, when: date, what: str) -> None:
    """날짜 입력에 직접 타이핑한다. 달력을 눌러 고르는 것보다 확실하다."""
    selector = _eval(page, FIND_DATE_INPUT_JS, label_re)
    if not selector:
        dump = _dump(page, f"dates-{what}", DUMP_DATE_INPUTS_JS)
        raise AttachError(
            f"{what} 날짜 입력칸을 찾지 못했습니다"
            + (f" (화면의 입력 목록: {dump})" if dump else "")
        )
    text = when.strftime("%Y-%m-%d")
    page.click(selector)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(text, delay=60)
    page.keyboard.press("Escape")  # 달력이 떠 있으면 닫는다. 다음 클릭을 가린다
    page.wait_for_timeout(500)


def _click_text(page, want: str, what: str, timeout: int = 20000) -> None:
    _wait_js(page, HAS_TEXT_JS, what, arg=want, timeout=timeout)
    _click_marked(page, FIND_BY_TEXT_JS, what, arg=want)
    page.wait_for_timeout(1200)


def _fill_itemization(page, amounts: list[int], dates: list[date], folder: Path | None) -> int:
    """일일 객실 요금을 채운다. 행 수가 숙박일수와 다르면 손대지 않는다.

    합이 경비 금액과 정확히 같아야 한다. 행이 하나라도 어긋나면 합이 틀어지고,
    틀어진 채로 저장하면 나중에 찾기 어렵다. 그래서 맞지 않으면 멈춘다.
    """
    rows = _eval(page, ITEMIZATION_ROWS_JS)
    filled = [r for r in rows if r["inputs"]]
    if len(filled) != len(amounts):
        dump = _dump(page, "itemization-rows", ITEMIZATION_ROWS_JS)
        raise AttachError(
            f"항목별 명세 표가 {len(filled)}행인데 숙박일수는 {len(amounts)}박입니다. "
            "행 수가 맞지 않으면 합계가 금액과 달라지므로 채우지 않았습니다."
            + (f" (표 정보: {dump})" if dump else "")
        )
    for r, money in zip(filled, amounts):
        target = r["inputs"][0]["selector"]  # 첫 금액 입력이 객실 요금이다
        page.fill(target, str(money))
    return len(amounts)


def parse_attendees(value: str) -> list[str]:
    """쉼표로 나눈다. 빈 항목은 버린다."""
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _attendee_input(page) -> str:
    """검색창 셀렉터. 닫혀 있으면 콤보박스를 눌러서 연다."""
    selector = _eval(page, ATTENDEE_INPUT_JS, DETAIL_FIELD_IDS)
    if selector:
        return selector
    _wait_js(page, ATTENDEE_COMBO_READY_JS, "참석자 검색 콤보박스", timeout=30000)
    # 실제 마우스로 눌러야 한다. JS click은 React가 안 듣는다.
    _click_marked(page, SELECT_ATTENDEE_COMBO_JS, "참석자 검색 콤보박스")
    _wait_js(page, ATTENDEE_INPUT_JS, "참석자 검색 입력창", arg=DETAIL_FIELD_IDS, timeout=15000)
    return _eval(page, ATTENDEE_INPUT_JS, DETAIL_FIELD_IDS)


def _pick_attendee(page, query: str) -> None:
    """한 명을 검색해서 고른다. 앞사람 검색어는 지우고 시작한다."""
    selector = _attendee_input(page)
    page.click(selector)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    # fill 대신 실제 타이핑. 자동완성은 키 입력을 보고 검색을 띄운다.
    page.keyboard.type(query, delay=80)

    # 타이핑이 실제로 그 칸에 들어갔는지 먼저 본다. 검색 결과가 안 뜨는 것과
    # 애초에 입력이 안 된 것은 원인이 다르다.
    _wait_js(
        page,
        "(a) => { const el = document.querySelector(a.sel);"
        " return !!el && (el.value || '').includes(a.q); }",
        f"'{query}' 가 입력창에 들어가는 것",
        arg={"sel": selector, "q": query},
        timeout=10000,
    )
    _wait_js(page, HAS_ATTENDEE_OPTION_JS, f"'{query}' 검색 결과", timeout=25000)
    _click_marked(page, SELECT_ATTENDEE_OPTION_JS, f"'{query}' 검색 결과")
    page.wait_for_timeout(1500)


def name_matches(query: str, name: str) -> bool:
    """검색어가 가리키는 사람과 화면의 이름이 같은가.

    검색어는 'kyungsik.oh', 화면 이름은 'Oh Kyungsik' 처럼 형태가 다르다.
    검색어를 토막 내서 전부 이름 안에 있으면 같은 사람으로 본다.
    """
    parts = [p for p in re.split(r"[^0-9A-Za-z가-힣]+", query.lower()) if p]
    low = name.lower()
    return bool(parts) and all(p in low for p in parts)


def _attendee_names(page) -> list[str]:
    """모달에 이미 올라와 있는 참석자 이름. 못 읽으면 빈 목록."""
    try:
        return [n for n in _eval(page, ATTENDEE_NAMES_JS) if n]
    except Exception:
        return []


def _sync_attendees(page, report_url: str, expense_id: str, queries: list[str]) -> int:
    """화면의 참석자를 작업지에 적힌 사람들과 맞춘다.

    이미 같으면 건드리지 않는다. 빠진 사람만 넣는다. 작업지에 없는 사람이
    올라와 있으면 지워야 하는데, 지우는 화면을 아직 확인하지 못했다. 조용히
    두면 잘못된 참석자가 그대로 남으므로 멈추고 사람에게 넘긴다.

    여러 명이면 한 명씩 검색·선택을 반복하고 저장은 마지막에 한 번만 한다.
    중간에 실패하면 아무것도 저장되지 않으므로 다시 돌리면 처음부터 다시 한다.
    """
    if not queries:
        return 0
    count = _eval(page, ATTENDEE_COUNT_JS)
    if count is None:
        raise AttachError("참석자 버튼을 찾지 못했습니다")

    # 주소로 모달을 열면 앱 전체를 다시 띄운다. 넉넉히 기다려야 한다.
    page.goto(
        f"{expense_url(report_url, expense_id)}?modal=attendees&context=entry",
        wait_until="domcontentloaded",
    )
    page.wait_for_timeout(2500)

    names = _attendee_names(page) if count else []
    extra = [n for n in names if not any(name_matches(q, n) for q in queries)]
    if extra:
        dump = _dump(page, "attendees-mismatch", ATTENDEE_NAMES_JS)
        raise AttachError(
            f"참석자가 작업지와 다릅니다. 화면: {', '.join(names)} / 작업지: {', '.join(queries)}. "
            "참석자를 지우는 것은 아직 자동으로 하지 못해서 그대로 두었습니다. "
            "이 건은 직접 수정해 주세요."
            + (f" (화면 정보: {dump})" if dump else "")
        )

    missing = [q for q in queries if not any(name_matches(q, n) for n in names)]
    if not missing:
        return 0

    try:
        for query in missing:
            _pick_attendee(page, query)
    except AttachError:
        _dump(page, "combos-attendee", DUMP_COMBOS_JS)
        _dump(page, "inputs-attendee", DUMP_INPUTS_JS)
        raise

    # exact=True 가 중요하다. 기본은 부분 일치라 '저장'이 '경비 저장'에도
    # 걸려서 모달 버튼 대신 뒤쪽 버튼을 누를 수 있다.
    page.get_by_role("button", name="저장", exact=True).first.click()

    # 참석자 수가 넣은 만큼 늘었는지로 확인한다. 모달이 닫혔는지나 주소가
    # 바뀌었는지는 추측이었고, 원래 확인하려던 것은 실제로 붙었는지다.
    try:
        _wait_js(
            page,
            "(want) => {" + ATTENDEE_COUNT_JS_BODY + " return n !== null && n >= want; }",
            f"참석자 {len(queries)}명이 추가되는 것",
            arg=len(queries),
            timeout=30000,
        )
    except AttachError:
        actual = _eval(page, ATTENDEE_COUNT_JS)
        _dump(page, "inputs-attendee-save", DUMP_INPUTS_JS)
        raise AttachError(f"참석자 {len(queries)}명을 넣으려 했는데 화면에는 {actual}명만 있습니다")
    return len(missing)


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

    if plan.purpose and _set_field(page, PURPOSE_FIELD, plan.purpose, "비즈니스 목적"):
        done.append("목적")
    if plan.comment and _set_field(page, COMMENT_FIELD, plan.comment, "코멘트"):
        done.append("코멘트")

    if plan.lodging:
        done += _apply_lodging(page, plan)

    if done:
        page.get_by_role("button", name="경비 저장", exact=True).first.click()
        # 저장이 끝나고 화면이 다시 그려질 때까지 기다린다. 여기서 서둘러
        # 참석자 모달로 넘어가면 방금 넣은 값이 날아간다.
        page.wait_for_timeout(2000)
        _wait_js(page, WAIT_AMOUNT_JS, "저장 후 화면", arg=str(row.amount))

    added = _sync_attendees(page, report_url, row.expense_id, parse_attendees(plan.attendee))
    if added:
        done.append(f"참석자 {added}명")

    return ", ".join(done) if done else "이미 되어 있음"


def _apply_lodging(page, plan: Plan) -> list[str]:
    """숙박비 상세와 항목별 명세를 채운다.

    순서를 지켜야 한다. 날짜 범위를 먼저 넣어야 항목별 명세 표가 그 날짜로
    만들어진다. 표가 먼저 만들어져 있으면 날짜를 바꿔도 행이 안 따라온다.
    """
    lodging = plan.lodging
    amounts = nightly_split(plan.row.amount or 0, lodging.nights)
    done = []

    _type_date(page, RE_CHECKIN, lodging.checkin, "체크인")
    _type_date(page, RE_CHECKOUT, lodging.checkout, "체크아웃")
    done.append(f"숙박 {_md(lodging.checkin)}~{_md(lodging.checkout)}")

    if lodging.location:
        _pick_from_combo(page, RE_LOCATION, lodging.location, "숙박위치")
        done.append("숙박위치")
    if lodging.channel:
        _pick_from_combo(page, RE_CHANNEL, lodging.channel, "Booking Channel")
        done.append("Booking Channel")

    # 상세를 먼저 저장한다. 저장하지 않고 탭을 옮기면 방금 넣은 날짜가 날아간다.
    page.get_by_role("button", name="경비 저장", exact=True).first.click()
    page.wait_for_timeout(2500)

    _click_text(page, TAB_ITEMIZATION, f"'{TAB_ITEMIZATION}' 탭")
    _pick_from_combo(page, RE_RECURRENCE, RECUR_DIFFERENT_DAILY, "반복")
    page.wait_for_timeout(1500)

    n = _fill_itemization(page, amounts, lodging.dates(), None)
    done.append(f"일일 객실 요금 {n}행 (합 {sum(amounts):,}원)")
    return done


def plans_from_sheet(cfg: dict, rows: list[Row], sheet_path: Path, tolerance: int):
    """작업지에 적힌 대로 계획을 만든다. 규칙 대신 사람이 정한 값을 쓴다."""
    entries = sheet.load(sheet_path)
    pairs, missing = match_rows(entries, rows, tolerance)
    plans = []
    for entry, row, how in pairs:
        code, label = None, row.expense_type
        # 화면 유형과 정확히 같을 때만 넘어간다. 예전에는 부분 일치로 봤는데
        # 다른 유형에 이름이 섞여 있으면 이미 바꾼 줄 알고 지나쳤다.
        if entry.type_name and entry.type_name != (row.expense_type or "").strip():
            code, label = settings.code_for(cfg, entry.type_name), entry.type_name
        lodging = None
        if entry.checkin and entry.checkout:
            lodging = Lodging(entry.checkin, entry.checkout, entry.location, entry.channel)
        plan = Plan(row, code, label, entry.purpose, entry.comment, entry.attendee, lodging)
        if plan.type_code or plan.fill_meal or plan.lodging:
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
            print(f"\n작업지에는 있으나 Concur에서 찾지 못한 것 {len(missing)}건:")
            for entry, why in missing:
                print(f"  {entry.when} {entry.amount:>9,}원  {entry.merchant[:16]} - {why}")
    else:
        plans = [p for p in (decide(r, rules) for r in rows if r.expense_id) if p]

    print(f"\n경비 {len(rows)}건 중 {len(plans)}건을 수정합니다\n")
    for plan in plans:
        r = plan.row
        print(f"  {r.when} {r.amount:>9,}원  {r.expense_type[:22]:22} -> {plan.summary()}")

    skipped = len(rows) - len(plans)
    if skipped:
        print(f"\n{skipped}건은 그대로 둡니다 (이미 맞거나 대상이 아닙니다)")

    if not apply:
        print("\n계획만 보여 드렸습니다. 실제로 반영하시려면 --apply 를 붙여 주세요.")
        return 0

    if limit:
        plans = plans[:limit]
        print(f"\n--limit {limit} 이라서 {len(plans)}건만 처리합니다.")

    done, failed = 0, []
    for i, plan in enumerate(plans, 1):
        try:
            what = apply_plan(page, plan, report_url)
            done += 1
            print(f"  [{i}/{len(plans)}] {plan.row.when} {plan.row.amount:,}원 - {what}")
        except (AttachError, PWTimeout) as exc:
            failed.append((plan, str(exc)))
            print(f"  [{i}/{len(plans)}] 실패했습니다 - {plan.row.when} {plan.row.amount:,}원: {exc}")

    print(f"\n{done}건을 처리했습니다.")
    if failed:
        print(f"{len(failed)}건은 처리하지 못했습니다:")
        for plan, why in failed:
            print(f"  ! {plan.row.when} {plan.row.amount:,}원: {why}")
        return 1
    return 0


def list_types_phase(page, report_url: str) -> int:
    usable = [r for r in read_rows(page) if r.expense_id]
    if not usable:
        raise AttachError("경비가 하나도 없습니다. 리포트를 열고 다시 시도해 주세요.")
    page.goto(expense_url(report_url, usable[0].expense_id), wait_until="domcontentloaded")
    _wait_js(page, TYPE_COMBO_READY_JS, "경비 유형 콤보박스", timeout=25000)
    _click_marked(page, SELECT_TYPE_COMBO_JS, "경비 유형 콤보박스")
    _wait_js(page, "() => !!document.querySelector('li[role=\"option\"]')", "경비 유형 목록")
    types = _eval(page, DUMP_TYPES_JS)
    print(f"\n경비유형 {len(types)}개를 찾았습니다. settings.json 의 expense_type_codes 에 넣어 쓰시면 됩니다.\n")
    for t in sorted(types, key=lambda x: x["label"]):
        print(f'  "{t["label"]}": "{t["code"]}",')
    return 0


def list_lodging_phase(page, cfg: dict) -> int:
    """숙박위치와 Booking Channel의 목록을 화면에서 뽑아 settings.json 에 넣는다.

    엑셀 드롭다운에 걸 값이라 화면과 한 글자도 다르면 안 된다. 손으로 옮겨
    적으면 틀린다. 사람이 숙박비 경비 상세를 열어두면 여기서 읽는다.
    """
    print("\n" + "=" * 64)
    print("  숙박비 경비 하나를 열어 주세요 (상세 화면이 보이는 상태).")
    print("  숙박위치와 Booking Channel 드롭다운이 보이면 Enter를 눌러 주세요.")
    print("=" * 64)
    console.wait_enter("숙박비 상세를 여셨으면 Enter > ")

    found = {}
    for key, label_re, what in (
        ("lodging_locations", RE_LOCATION, "숙박위치"),
        ("booking_channels", RE_CHANNEL, "Booking Channel"),
    ):
        try:
            _wait_js(page, COMBO_READY_JS, f"{what} 콤보박스", arg=label_re, timeout=15000)
            _click_marked(page, SELECT_COMBO_JS, f"{what} 콤보박스", arg=label_re)
            _wait_js(page, "() => !!document.querySelector('li[role=\"option\"]')",
                     f"{what} 목록", timeout=15000)
            options = _eval(page, READ_OPTIONS_JS)
        except AttachError as exc:
            dump = _dump(page, "combos-lodging", DUMP_COMBOS_JS)
            print(f"  {what}: 못 읽었습니다 - {exc}" + (f" (콤보박스 목록: {dump})" if dump else ""))
            continue
        page.keyboard.press("Escape")  # 목록을 닫아야 다음 콤보박스를 누를 수 있다
        page.wait_for_timeout(800)
        found[key] = options
        print(f"  {what}: {len(options)}개")
        for option in options:
            print(f"    - {option}")

    if not found:
        raise AttachError("드롭다운을 하나도 읽지 못했습니다. 숙박비 상세가 맞는지 확인해 주세요.")
    cfg.update(found)
    settings.save(cfg)
    print(f"\n{settings.SETTINGS_PATH} 에 저장했습니다. 이제 B단계를 다시 돌리시면")
    print("작업지의 숙박위치·Booking Channel 칸에 드롭다운이 걸립니다.")
    return 0


def run(apply: bool, limit: int | None, list_types: bool = False,
        sheet_path: Path | None = None, list_lodging: bool = False) -> int:
    cfg = settings.load()
    pw, ctx, page, report_url = open_report()
    try:
        if list_types:
            return list_types_phase(page, report_url)
        if list_lodging:
            return list_lodging_phase(page, cfg)
        return fix_phase(page, report_url, cfg, apply, limit, sheet_path)
    finally:
        ctx.close()
        pw.stop()


def main() -> int:
    console.setup()
    ap = argparse.ArgumentParser(description="Concur 경비유형·참석자·목적·코멘트 채우기")
    ap.add_argument("--apply", action="store_true", help="실제로 반영합니다")
    ap.add_argument("--limit", type=int, help="앞에서 N건만 처리합니다 (동작 확인용)")
    ap.add_argument("--sheet", nargs="?", const="", type=str,
                    help="작업지(csv/xlsx)대로 넣습니다. 값 없이 주시면 전표 폴더의 manifest.csv 를 씁니다")
    ap.add_argument("--list-types", action="store_true",
                    help="화면의 경비유형과 코드를 뽑습니다 (새 유형이 생겼을 때 쓰세요)")
    ap.add_argument("--list-lodging", action="store_true",
                    help="숙박위치·Booking Channel 목록을 뽑아 settings.json 에 넣습니다")
    args = ap.parse_args()
    try:
        path = None
        if args.sheet is not None:
            path = Path(args.sheet) if args.sheet else Path(settings.load()["downloads_dir"]) / "manifest.csv"
        return run(args.apply, args.limit, args.list_types, path, args.list_lodging)
    except (AttachError, sheet.SheetError) as exc:
        print(f"\n작업을 중단했습니다: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
