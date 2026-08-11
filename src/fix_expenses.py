"""Concur 경비의 경비유형·참석자·목적·코멘트를 채운다.

로그인(SSO)과 리포트 열기는 사람이 한다. 그 다음 목록을 읽어 작업지대로 고친다.

    python -m src.fix_expenses --sheet                     # 계획만 (아무것도 안 바꿈)
    python -m src.fix_expenses --sheet --apply --limit 1   # 한 건만 해보고 확인
    python -m src.fix_expenses --sheet --apply             # 나머지 전부

작업지(엑셀)가 유일한 기준이다. 다시 돌리면 유형·목적·코멘트는 적힌 값으로 다시
맞추고(이미 같으면 건드리지 않는다), 참석자는 화면과 견줘서 없는 사람은 지우고
빠진 사람만 넣는다. 빈 칸은 '건드리지 마라'는 뜻이다.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import TimeoutError as PWTimeout

from . import console, paths, settings, sheet
from .sheet import nightly_split
from .attach_receipts import dump_rows, print_unreadable
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

PROFILE_DIR = paths.at("browser-profile", "concur")

LABEL_MEAL = "내부 직원간 식음료"

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

# 검색어와 맞는 결과를 고른다. 그냥 첫 번째를 고르면 두 가지가 어긋난다.
#   - 결과가 여럿일 때 엉뚱한 사람이 들어간다.
#   - 앞사람 검색 결과가 아직 지워지지 않았을 때 그 사람을 또 넣는다.
# 파이썬의 name_matches 와 같은 규칙이다: 검색어를 토막 내서 전부 들어 있으면 같다.
MATCH_NAME_FN = """
  const looksLike = (query, text) => {
    const parts = query.toLowerCase().split(/[^0-9a-z가-힣]+/i).filter(Boolean);
    const low = (text || '').toLowerCase();
    return parts.length > 0 && parts.every(p => low.includes(p));
  };
"""

FIND_ATTENDEE_OPTION_FN = (
    REAL_ATTENDEE_FN
    + MATCH_NAME_FN
    + """
  const findOption = (q) => [...document.querySelectorAll('li[role="option"]')]
    .filter(isRealAttendee)
    .find(o => looksLike(q, o.innerText));
"""
)

HAS_ATTENDEE_OPTION_JS = (
    "(q) => {" + FIND_ATTENDEE_OPTION_FN + " return !!findOption(q); }"
)

SELECT_ATTENDEE_OPTION_JS = (
    "(q) => {" + MARK_FN + FIND_ATTENDEE_OPTION_FN + " return mark(findOption(q)); }"
)


# 참석자 버튼은 '참석자 (0)' 처럼 개수를 달고 있다.
ATTENDEE_COUNT_JS_BODY = """
  const b = [...document.querySelectorAll('button')]
    .find(x => /^참석자\\s*\\(\\d+\\)/.test((x.innerText || '').trim()));
  const n = b ? parseInt((b.innerText.match(/\\((\\d+)\\)/) || [])[1], 10) : null;
"""

ATTENDEE_COUNT_JS = "() => {" + ATTENDEE_COUNT_JS_BODY + " return n; }"

# 참석자 모달 (실측 2026-08). 표가 <table>이 아니라 div[role="table"]이다.
# tbody tr 로 찾다가 한 줄도 못 읽었고, 그래서 이미 맞게 들어 있는 참석자를
# '다르다'고 판단했다. 훅이 다 붙어 있으니 짐작할 필요가 없다.
ATTENDEE_SAVE = '[data-nuiexp="sat-btn-save"]'
ATTENDEE_CANCEL = '[data-nuiexp="sat-btn-cancel"]'
ATTENDEE_REMOVE = '[data-nuiexp="sat-btn-remove-attendee"]'

ATTENDEE_ROWS_FN = """
  const modal = document.querySelector('[data-nuiexp="attendees-dialog"]');
  const rows = () => modal
    ? [...modal.querySelectorAll('[role="row"][data-testid="data-row"]')]
    : [];
  const nameOf = (r) => {
    const n = r.querySelector('[data-nuiexp="name"]');
    return n ? (n.innerText || '').trim() : '';
  };
"""

ATTENDEE_NAMES_JS = (
    "() => {" + ATTENDEE_ROWS_FN + " return rows().map(nameOf).filter(Boolean); }"
)

# 고른 사람이 실제로 표에 올라왔는지 본다. 눌렀다고 들어간 것은 아니다.
HAS_ATTENDEE_ROW_JS = (
    "(q) => {" + ATTENDEE_ROWS_FN + MATCH_NAME_FN
    + " return rows().some(r => looksLike(q, nameOf(r))); }"
)

# 지울 사람의 체크박스를 고른다. 그 다음 툴바의 '제거'를 누른다. 행마다 있는
# ... 메뉴를 여는 것보다 단계가 적고, 여러 명을 한 번에 지울 수도 있다.
CHECK_ATTENDEE_JS = (
    "(want) => {"
    + MARK_FN
    + ATTENDEE_ROWS_FN
    + """
  const row = rows().find(r => nameOf(r) === want);
  if (!row) return null;
  return mark(row.querySelector('[data-testid="selection-cell"] input[type="checkbox"]'));
}"""
)

DUMP_ATTENDEE_MODAL_JS = (
    "() => {"
    + ATTENDEE_ROWS_FN
    + """
  return {
    isModal: !!modal,
    names: rows().map(nameOf),
    html: (modal || document.body).outerHTML.slice(0, 40000),
  };
}"""
)

SELECT_ATTENDEE_BUTTON_JS = "() => {" + MARK_FN + ATTENDEE_COUNT_JS_BODY + " return mark(b); }"

# --- 숙박비 -----------------------------------------------------------------
#
# 아래 훅은 전부 실제 화면 덤프에서 확인한 것이다 (2026-08, inspect_page).
# 숙박 위치와 Booking channel은 정책이 정한 사용자 정의 필드라 id가 고정이다.
# 그래도 id만 믿지 않고 aria-label로도 찾게 해둔다 - 정책이 바뀌면 번호가 바뀐다.
COMBO_BY_HINT_FN = """
  const findByHint = (hint) => {
    const byId = document.getElementById(hint);
    if (byId && byId.getAttribute('role') === 'combobox') return byId;
    const re = new RegExp(hint, 'i');
    return [...document.querySelectorAll('[role="combobox"]')].find(c => {
      const aria = c.getAttribute('aria-label') || '';
      const by = c.getAttribute('aria-labelledby');
      const text = by
        ? by.split(/\\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ')
        : '';
      return re.test(aria) || re.test(text) || !!c.querySelector('[name="' + hint + '"]');
    });
  };
"""

COMBO_READY_JS = "(hint) => {" + COMBO_BY_HINT_FN + " return !!findByHint(hint); }"

SELECT_COMBO_JS = (
    "(hint) => {" + COMBO_BY_HINT_FN + MARK_FN + " return mark(findByHint(hint)); }"
)

COMBO_VALUE_JS = (
    "(hint) => {"
    + COMBO_BY_HINT_FN
    + " const c = findByHint(hint); return c ? (c.innerText || '').trim() : null; }"
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

# 날짜는 입실·퇴실이 따로가 아니라 '날짜 범위' 한 칸이다.
#   <input id="hotelCheckinDate-date-input-field-input"
#          placeholder="YYYY-MM-DD - YYYY-MM-DD">
# 달력 버튼이 붙어 있지만 입력창 자체가 타이핑을 받는다. 달력을 눌러 고르는
# 것보다 확실하다.
DATE_RANGE_FIELD = "#hotelCheckinDate-date-input-field-input"

# 탭은 id가 붙어 있다. 글자로 찾을 필요가 없다.
TAB_DETAILS = "#details-tab"
TAB_ITEMIZATION = "#itemizations-tab"

# 항목별 명세의 객실 요금 입력. id가 '<종류>Itemization.roomRate.<행번호>' 꼴이다
# (실측: SameRoomRateItemization.roomRate.0). '일일 금액 다름'으로 바꾸면 앞부분이
# 달라질 수 있어서 뒤쪽 모양만 본다. 세금 칸(taxRate)은 건드리지 않는다.
ROOM_RATE_INPUTS_JS = """
() => [...document.querySelectorAll('input')]
  .map(x => ({ key: x.id || x.getAttribute('name') || '', el: x }))
  .filter(o => /Itemization\\.roomRate\\.\\d+$/.test(o.key))
  .map(o => ({
    index: parseInt(o.key.match(/(\\d+)$/)[1], 10),
    selector: '#' + CSS.escape(o.el.id),
    value: (o.el.value || '').trim(),
    locked: o.el.disabled || o.el.readOnly || o.el.getAttribute('aria-disabled') === 'true',
  }))
  .sort((a, b) => a.index - b.index)
"""

# 표가 안 맞을 때 남길 근거. 행마다 첫 칸이 날짜다.
DUMP_ITEMIZATION_JS = """
() => [...document.querySelectorAll('tr[role="row"]')].map(r => ({
  text: (r.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 80),
  inputs: [...r.querySelectorAll('input')].map(x => x.id || x.getAttribute('name')),
}))
"""

# 저장하면 확인창이 뜬다. 숙박비는 항목별 명세를 채우기 전이라 필수값이 비어
# 있고, Concur가 '지금 수정하시겠습니까? 예/아니오' 를 묻는다. 이 창이 떠 있는
# 동안은 탭도 못 누른다. '아니오'로 닫고 우리가 항목별 명세로 간다.
HAS_DIALOG_JS = """
() => [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
  .some(d => d.getBoundingClientRect().width > 0)
"""

DIALOG_BUTTON_JS = (
    "(csv) => {"
    + MARK_FN
    + """
  const want = csv.split(',');
  const dlg = [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
    .find(d => d.getBoundingClientRect().width > 0);
  if (!dlg) return null;
  const buttons = [...dlg.querySelectorAll('button')];
  const text = (b) => (b.innerText || '').trim();
  // 정확히 맞는 것을 먼저 본다. 없으면 '아니'로 시작하는 것을 찾는다 -
  // 화면 문구가 '아니오'인지 '아니요'인지에 걸려 못 닫은 적이 있다.
  return mark(
    buttons.find(b => want.includes(text(b)))
    || buttons.find(b => /^(아니|No\b)/i.test(text(b)))
  );
}"""
)

DUMP_DIALOG_JS = """
() => [...document.querySelectorAll('[role="dialog"], [role="alertdialog"]')]
  .filter(d => d.getBoundingClientRect().width > 0)
  .map(d => ({
    text: (d.innerText || '').trim().slice(0, 300),
    buttons: [...d.querySelectorAll('button')].map(b => (b.innerText || '').trim()),
  }))
"""

# '아니오'를 누르면 리포트 목록으로 돌아가버린다(실측). 저장 자체는 이미 됐으니
# 값이 날아가지는 않지만, 화면이 바뀌므로 다음 단계는 경비를 다시 열고 시작해야
# 한다. 그래서 이 창을 닫은 뒤에는 늘 주소로 상세를 다시 연다.
DIALOG_DISMISS = "아니요,아니오,No,닫기,취소"

# 저장 버튼은 탭마다 이름이 다르다. 상세 정보는 '경비 저장', 항목별 명세는
# '저장'이다. 앞에 적은 이름부터 찾는다. get_by_role(name=...)은 기본이 부분
# 일치라 '저장'이 '경비 저장'에도 걸려서 엉뚱한 버튼을 누른다 - 여기서는
# 정확히 같은 글자만 본다.
#
# 글자가 같은 버튼이 여럿이다. 하나는 화면에 안 보이게 숨겨둔 것이라 누를 수
# 없다(실측: data-nuiexp="exp-save-expense-hidden", class에 save-hidden-button,
# "element is outside of the viewport"). 그렇다고 이런 버튼을 목록에서 빼버리면
# 안 된다 - 빼고 나니 '저장 버튼을 찾지 못했습니다'로 그냥 넘어가버렸다.
# 후보를 전부 모아 누를 만한 것부터 차례로 눌러 본다.
SAVE_BUTTONS_JS = """
(csv) => {
  document.querySelectorAll('[data-auto-save]')
    .forEach(e => e.removeAttribute('data-auto-save'));
  const hidden = (b) => {
    const cls = typeof b.className === 'string' ? b.className : '';
    return /save-hidden-button/.test(cls) || /-hidden$/.test(b.getAttribute('data-nuiexp') || '');
  };
  const buttons = [...document.querySelectorAll('button')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !b.disabled;
  });
  const text = (b) => (b.innerText || '').trim();
  const found = [];
  for (const want of csv.split(',')) {
    for (const b of buttons) {
      if (text(b) === want && !found.includes(b)) found.push(b);
    }
  }
  // 숨긴 버튼은 뒤로 민다. 앞의 것이 눌리면 거기까지 가지도 않는다.
  found.sort((a, b) => (hidden(a) ? 1 : 0) - (hidden(b) ? 1 : 0));
  return found.map((b, i) => {
    b.setAttribute('data-auto-save', String(i));
    return '[data-auto-save="' + i + '"]';
  });
}
"""

# 못 눌렀을 때 남길 근거. 글자만 남기면 같은 글자 버튼 중 무엇이 문제였는지
# 알 수 없다. 어떤 버튼인지(hook, class)와 어디에 있는지까지 남긴다.
DUMP_BUTTONS_JS = """
() => [...document.querySelectorAll('button')]
  .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
  .map(b => {
    const r = b.getBoundingClientRect();
    return {
      text: (b.innerText || '').trim().slice(0, 40),
      disabled: b.disabled,
      hook: b.getAttribute('data-nuiexp') || null,
      cls: (typeof b.className === 'string' ? b.className : '').slice(0, 90),
      at: [Math.round(r.left), Math.round(r.top)],
      onScreen: r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth,
    };
  })
"""

SAVE_DETAIL = "경비 저장,저장"
SAVE_ITEMIZATION = "저장,항목별 명세 저장,경비 저장"

LABEL_LODGING = "숙박비"
RECUR_DIFFERENT_DAILY = "일일 금액 다름"

# 콤보박스를 찾는 실마리. id를 먼저 보고, 없으면 라벨로 찾는다.
HINT_LOCATION = "custom10"  # 숙박 위치 (국내 / 해외)
HINT_CHANNEL = "custom16"  # Booking channel
HINT_RECURRENCE = "recurrence"  # 반복 (일일 금액 동일 / 다름)

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


def _dump(page, tag: str, script: str) -> str | None:
    """화면 상태를 남긴다. 추측 대신 근거로 고치기 위해서다."""
    try:
        data = _eval(page, script)
    except Exception:
        return None
    out = paths.at("inspect-out")
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


def _set_field(page, selector: str, value: str, what: str, required: bool = True) -> bool | None:
    """작업지에 적힌 값으로 맞춘다. 이미 다른 값이 있으면 덮어쓴다.

    작업지가 유일한 기준이라 화면에 뭐가 적혀 있든 그쪽으로 맞춘다. 이미
    같은 값이면 건드리지 않는다 - 저장 한 번을 아낀다.
    """
    try:
        page.wait_for_selector(selector, timeout=20000)
    except PWTimeout:
        if not required:
            return None  # 이 유형에는 없는 칸이다 (숙박비에는 비즈니스 목적이 없다)
        # 조용히 넘기면 안 된다. 안 채워졌는데 채운 줄 알게 된다.
        raise AttachError(f"{what} 입력칸을 찾지 못했습니다") from None
    if page.input_value(selector).strip() == value.strip():
        return False
    page.fill(selector, value)
    return True


def _pick_from_combo(page, hint: str, want: str, what: str) -> bool:
    """콤보박스를 찾아 열고, 보이는 글자로 옵션을 고른다. 바꿨으면 True.

    hint는 필드 id(custom10 같은 것)이거나 라벨의 일부다. id를 먼저 보고
    없으면 라벨로 찾는다. 정책이 바뀌어 번호가 달라져도 라벨로 걸린다.
    """
    # 이미 그 값이면 건드리지 않는다. 잠겨 있는 콤보박스를 눌러 실패하는 일도 막는다.
    if want in (_eval(page, COMBO_VALUE_JS, hint) or ""):
        return False

    try:
        _wait_js(page, COMBO_READY_JS, f"{what} 콤보박스", arg=hint, timeout=25000)
    except AttachError:
        dump = _dump(page, f"combos-{what}", DUMP_COMBOS_JS)
        raise AttachError(
            f"{what} 콤보박스를 찾지 못했습니다" + (f" (화면의 콤보박스 목록: {dump})" if dump else "")
        ) from None
    _click_marked(page, SELECT_COMBO_JS, f"{what} 콤보박스", arg=hint)

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
    page.wait_for_timeout(600)

    # 고른 값이 실제로 들어갔는지 본다. 눌렀다고 바뀐 것은 아니다.
    shown = _eval(page, COMBO_VALUE_JS, hint) or ""
    if want not in shown:
        raise AttachError(f"{what}이(가) '{want}' 로 바뀌지 않았습니다 (화면: '{shown.strip()}')")
    return True


def _set_date_range(page, checkin: date, checkout: date) -> bool:
    """'날짜 범위' 한 칸에 입실과 퇴실을 함께 넣는다. 바꿨으면 True.

    입실·퇴실이 따로 있는 게 아니라 한 입력이다. 화면이 알려주는 형식은
    'YYYY-MM-DD - YYYY-MM-DD' 라 그대로 친다. 달력을 눌러 고르는 것보다 확실하다.

    이미 그 날짜면 건드리지 않는다. 다시 돌릴 때 같은 값을 또 쳐 넣으면
    바뀐 것이 없는데도 저장을 해야 하고, 저장할 일이 없는 화면에서 저장
    버튼을 찾다가 실패한다.
    """
    want = f"{checkin:%Y-%m-%d} - {checkout:%Y-%m-%d}"
    try:
        page.wait_for_selector(DATE_RANGE_FIELD, timeout=20000)
    except PWTimeout:
        dump = _dump(page, "inputs-lodging", DUMP_INPUTS_JS)
        raise AttachError(
            "날짜 범위 입력칸을 찾지 못했습니다"
            + (f" (화면의 입력 목록: {dump})" if dump else "")
        ) from None

    already = page.input_value(DATE_RANGE_FIELD).strip()
    if checkin.strftime("%Y-%m-%d") in already and checkout.strftime("%Y-%m-%d") in already:
        return False

    page.click(DATE_RANGE_FIELD)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(want, delay=50)
    page.keyboard.press("Escape")  # 달력이 떠 있으면 닫는다. 다음 클릭을 가린다
    page.wait_for_timeout(500)

    # 넣은 대로 남았는지 본다. 달력이 값을 다시 쓰는 경우가 있다.
    shown = page.input_value(DATE_RANGE_FIELD).strip()
    if checkin.strftime("%Y-%m-%d") not in shown or checkout.strftime("%Y-%m-%d") not in shown:
        raise AttachError(f"날짜 범위가 '{want}' 로 들어가지 않았습니다 (화면: '{shown}')")
    return True


def _dismiss_dialog(page, wait_ms: int = 2500) -> str | None:
    """저장 후 뜨는 확인창을 닫는다. 안 뜨면 아무것도 하지 않는다.

    '이 경비가 저장되었지만 필수 정보가 누락되었습니다. 지금 수정하시겠습니까?'
    가 대표적이다. 숙박비는 항목별 명세를 채우기 전이라 늘 이 창이 뜬다.
    떠 있는 동안은 탭도 못 눌러서 다음 단계로 넘어갈 수 없다.
    """
    try:
        page.wait_for_function(HAS_DIALOG_JS, timeout=wait_ms)
    except PWTimeout:
        return None  # 창이 안 떴다. 정상이다

    dialogs = _eval(page, DUMP_DIALOG_JS) or [{}]
    text = (dialogs[0].get("text") or "").replace("\n", " ")[:60]
    selector = _eval(page, DIALOG_BUTTON_JS, DIALOG_DISMISS)
    if not selector:
        dump = _dump(page, "dialog", DUMP_DIALOG_JS)
        raise AttachError(
            f"저장 후 뜬 창을 닫지 못했습니다: '{text}'"
            + (f" (창 정보: {dump})" if dump else "")
        )
    page.click(selector)
    page.wait_for_timeout(800)
    return text


def _save_expense(page, row: Row, report_url: str, labels: str = SAVE_DETAIL,
                  reopen: bool = True) -> None:
    """저장하고, 뒤따라 뜨는 확인창까지 처리한다.

    확인창을 닫으면 리포트 목록으로 튕겨 나간다(실측). 저장은 이미 끝났으니
    값은 남아 있다. 이 경비에서 할 일이 더 남았으면(reopen) 주소로 상세를 다시
    열고 금액으로 맞는 경비인지 확인한다. 마지막 저장이면 다시 열지 않는다 -
    어차피 다음 경비로 넘어가므로 그 왕복이 헛걸음이다.

    labels는 찾을 버튼 이름들이다. 탭마다 다르다 - 상세 정보는 '경비 저장',
    항목별 명세는 '저장'. 여기 없는 이름이면 화면의 버튼을 파일로 남기고 멈춘다.

    같은 글자의 버튼이 여럿이면 차례로 눌러 본다. 하나가 못 눌리는 것(화면 밖에
    숨겨둔 버튼)과 저장할 수 없는 것은 다르다. 하나도 못 누르면 멈춘다 -
    저장이 안 된 것을 넘어가면 안 된다.
    """
    selectors = _eval(page, SAVE_BUTTONS_JS, labels) or []
    if not selectors:
        dump = _dump(page, "buttons", DUMP_BUTTONS_JS)
        raise AttachError(
            f"저장 버튼({labels})을 찾지 못했습니다"
            + (f" (화면의 버튼 목록: {dump})" if dump else "")
        )
    for selector in selectors:
        try:
            page.click(selector, timeout=10000)
            break
        except PWTimeout:
            continue
    else:
        dump = _dump(page, "buttons", DUMP_BUTTONS_JS)
        raise AttachError(
            f"저장 버튼({labels})을 {len(selectors)}개 찾았지만 하나도 누르지 못했습니다"
            + (f" (화면의 버튼 목록: {dump})" if dump else "")
        )
    page.wait_for_timeout(800)
    # 넣을 것을 다 넣고 저장하므로 확인창은 안 뜨는 것이 정상이다. 뜰 때만
    # 짧게 잡는다 - 매번 오래 기다리면 건마다 그만큼 늦어진다.
    told = _dismiss_dialog(page, wait_ms=1500)
    if told:
        print(f"     (저장 후 안내창을 닫았습니다: {told})")

    if reopen:
        page.goto(expense_url(report_url, row.expense_id), wait_until="domcontentloaded")
        _wait_js(page, WAIT_AMOUNT_JS, f"{row.amount:,}원 경비 상세", arg=str(row.amount))


def _open_tab(page, selector: str, what: str) -> None:
    try:
        page.wait_for_selector(selector, timeout=20000)
    except PWTimeout:
        raise AttachError(f"{what} 탭을 찾지 못했습니다") from None
    page.click(selector)
    page.wait_for_timeout(800)


# 항목별 명세 탭은 세 가지 얼굴이 있다(실측).
#   1) 입력 폼   - '반복' 콤보박스나 객실 요금 칸이 있다. 바로 채운다.
#   2) 빈 화면   - '항목별 명세 없음' 그림과 '항목별 명세 추가' 버튼만 있다.
#                  버튼을 눌러야 1)이 나온다.
#   3) 이미 있음 - 명세 표가 그려져 있다. 건드리지 않는다.
# 2)를 1)로 잘못 보고 '반복'을 25초 동안 찾다 실패한 적이 있다. 그래서 '무엇이
# 보이는가'를 먼저 읽고 나서 무엇을 할지 정한다.
EMPTY_ITEMIZATION_TEXT = "항목별 명세 없음"
ADD_ITEMIZATION_TEXT = "항목별 명세 추가"

ITEMIZATION_STATE_JS = (
    """
(() => {
  const rates = [...document.querySelectorAll('input')]
    .some(x => /Itemization\\.roomRate\\.\\d+$/.test(x.id || x.getAttribute('name') || ''));
  const add = [...document.querySelectorAll('button')].some(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && (b.innerText || '').includes('"""
    + ADD_ITEMIZATION_TEXT
    + """');
  });
  return {
    form: rates || !!document.querySelector('[name="recurrence"]'),
    add: add,
    empty: (document.body.innerText || '').includes('"""
    + EMPTY_ITEMIZATION_TEXT
    + """'),
  };
})
"""
)

# '추가' 버튼은 명세가 이미 있을 때도 있다(한 건 더 넣으라고). 그래서 이 버튼을
# 누르는 것은 화면이 '항목별 명세 없음'이라고 말할 때뿐이다 - 잘못 누르면 필요
# 없는 명세가 하나 더 생긴다.
ADD_ITEMIZATION_JS = (
    "() => {"
    + MARK_FN
    + """
  const buttons = [...document.querySelectorAll('button')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !b.disabled;
  });
  return mark(buttons.find(b => (b.innerText || '').includes('"""
    + ADD_ITEMIZATION_TEXT
    + """')));
}"""
)

# 어느 얼굴인지 알 수 있을 때까지 기다린다. '추가' 버튼은 2)와 3) 양쪽에 있어서
# 탭이 그려졌다는 신호로 쓰기 좋다.
ITEMIZATION_SETTLED_JS = (
    "() => { const s = (" + ITEMIZATION_STATE_JS.strip() + ")(); return s.form || s.add || s.empty; }"
)


def needs_recurrence(nights: int, on_screen) -> bool:
    """'반복'(일일 금액 동일/다름)을 골라야 하는가.

    1박은 반복할 밤이 없어서 이 칸이 아예 안 나온다. 없는 콤보박스를 25초
    찾다가 건 전체가 실패했다. 여러 박이면 반드시 있어야 하니 그때는 기다리고,
    1박은 화면에 있을 때만 고른다 - 한 행뿐이라 동일이든 다름이든 결과가 같다.

    on_screen은 화면을 보는 함수다. 1박일 때만 부른다 - 여러 박이면 볼 것도
    없이 골라야 하고, 아직 안 그려졌을 때 미리 봐서 없다고 판단하면 안 된다.
    """
    return nights > 1 or bool(on_screen())


def itemization_step(state: dict) -> str:
    """화면 상태를 보고 다음에 할 일을 정한다: 'fill' / 'add' / 'skip'.

    '추가' 버튼만으로 판단하면 안 된다. 명세가 이미 있을 때도 (한 건 더 넣으라고)
    같은 버튼이 있어서, 누르면 필요 없는 명세가 하나 더 생긴다. 누르는 것은
    화면이 '항목별 명세 없음'이라고 말할 때뿐이다.
    """
    if state.get("form"):
        return "fill"
    if state.get("empty"):
        return "add"
    return "skip"


def _itemization_ready(page) -> str | None:
    """객실 요금을 채울 수 있는 상태로 만든다. 못 채우면 그 이유를 돌려준다.

    빈 화면이면 '항목별 명세 추가'를 눌러 입력 폼을 띄운다. 이미 명세가
    들어가 있으면 건드리지 않는다 - 사람이 넣어둔 값을 우리가 다시 쓸 이유가
    없고, 요금 칸도 수정 불가다.
    """
    try:
        _wait_js(page, ITEMIZATION_SETTLED_JS, "항목별 명세 화면", timeout=15000)
    except AttachError:
        dump = _dump(page, "itemization-panel", DUMP_BUTTONS_JS)
        raise AttachError(
            "항목별 명세 화면이 그려지지 않았습니다"
            + (f" (화면의 버튼 목록: {dump})" if dump else "")
        ) from None

    step = itemization_step(_eval(page, ITEMIZATION_STATE_JS))
    if step == "skip":
        return "항목별 명세가 이미 있어 건드리지 않았습니다"
    if step == "add":
        _click_marked(page, ADD_ITEMIZATION_JS, f"'{ADD_ITEMIZATION_TEXT}' 버튼")
        try:
            _wait_js(
                page,
                "() => (" + ITEMIZATION_STATE_JS.strip() + ")().form",
                "항목별 명세 입력 화면",
                timeout=20000,
            )
        except AttachError:
            dump = _dump(page, "itemization-panel", DUMP_BUTTONS_JS)
            raise AttachError(
                f"'{ADD_ITEMIZATION_TEXT}'를 눌렀는데 입력 화면이 나오지 않았습니다"
                + (f" (화면의 버튼 목록: {dump})" if dump else "")
            ) from None

    cells = _eval(page, ROOM_RATE_INPUTS_JS)
    if not cells:
        return None  # 폼은 떴고 표는 '반복'을 골라야 생긴다
    if all(c["locked"] for c in cells):
        return "항목별 명세가 수정 불가라 건드리지 않았습니다"
    if all(c["value"] for c in cells):
        return "항목별 명세가 이미 채워져 있어 건드리지 않았습니다"
    return None


def _fill_room_rates(page, amounts: list[int]) -> str:
    """일일 객실 요금을 채운다. 채운 내용을 한 줄로 돌려준다.

    합이 경비 금액과 정확히 같아야 한다. 행이 하나라도 어긋나면 합이 틀어지고,
    틀어진 채로 저장하면 나중에 찾기 어렵다. 그래서 맞지 않으면 멈춘다.
    세금 칸은 건드리지 않는다 - 우리가 아는 값이 아니다.
    """
    _wait_js(
        page,
        "() => [...document.querySelectorAll('input')]"
        ".some(x => /Itemization\\.roomRate\\.\\d+$/.test(x.id || x.name || ''))",
        "객실 요금 표",
        timeout=25000,
    )
    cells = _eval(page, ROOM_RATE_INPUTS_JS)
    if len(cells) != len(amounts):
        dump = _dump(page, "itemization-rows", DUMP_ITEMIZATION_JS)
        raise AttachError(
            f"객실 요금 칸이 {len(cells)}개인데 숙박일수는 {len(amounts)}박입니다. "
            "수가 맞지 않으면 합계가 금액과 달라지므로 채우지 않았습니다. "
            "표가 옛 날짜로 만들어졌을 수 있습니다 - 상세 정보 탭의 날짜 범위를 "
            "확인해 주세요."
            + (f" (표 정보: {dump})" if dump else "")
        )
    for cell, money in zip(cells, amounts):
        page.fill(cell["selector"], str(money))
        page.wait_for_timeout(150)
    return f"일일 객실 요금 {len(amounts)}행 (합 {sum(amounts):,}원)"


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
    _wait_js(page, HAS_ATTENDEE_OPTION_JS, f"'{query}' 검색 결과", arg=query, timeout=25000)
    _click_marked(page, SELECT_ATTENDEE_OPTION_JS, f"'{query}' 검색 결과", arg=query)

    # 표에 올라온 것을 보고 다음 사람으로 넘어간다. 확인 없이 넘어가면 앞사람이
    # 덜 들어간 채로 다음 검색어를 치게 되고, 그 상태를 알아채지 못한다.
    _wait_js(page, HAS_ATTENDEE_ROW_JS, f"'{query}' 가 목록에 올라오는 것",
             arg=query, timeout=15000)


def name_matches(query: str, name: str) -> bool:
    """검색어가 가리키는 사람과 화면의 이름이 같은가.

    검색어는 'kyungsik.oh', 화면 이름은 'Oh Kyungsik' 처럼 형태가 다르다.
    검색어를 토막 내서 전부 이름 안에 있으면 같은 사람으로 본다.
    """
    parts = [p for p in re.split(r"[^0-9A-Za-z가-힣]+", query.lower()) if p]
    low = name.lower()
    return bool(parts) and all(p in low for p in parts)


def _open_attendee_modal(page, report_url: str, row: Row) -> None:
    """참석자 모달을 연다. 화면의 '참석자 (N)' 버튼을 누르는 것이 먼저다.

    주소로 열면 앱 전체가 다시 뜨면서 저장하지 않은 입력이 날아간다. 그래서
    전에는 먼저 저장해야 했고, 저장하면 '필수 정보가 누락되었습니다' 창이
    떴다(참석자를 아직 안 넣었으니까). 버튼으로 열면 그 과정이 통째로 없다.

    버튼으로 안 열리면 예전 방식으로 물러선다 - 저장하고 주소로 연다.
    """
    selector = _eval(page, SELECT_ATTENDEE_BUTTON_JS)
    if selector:
        page.click(selector)
        try:
            _wait_js(page, ATTENDEE_COMBO_READY_JS, "참석자 검색 콤보박스", timeout=10000)
            return
        except AttachError:
            pass  # 버튼이 모달을 여는 것이 아니었다. 주소로 연다

    # 주소로 열면 저장 안 한 입력이 날아간다. 먼저 저장한다. 어차피 곧 주소로
    # 넘어가므로 상세를 다시 열 필요는 없다.
    _save_expense(page, row, report_url, reopen=False)
    page.goto(
        f"{expense_url(report_url, row.expense_id)}?modal=attendees&context=entry",
        wait_until="domcontentloaded",
    )
    _wait_js(page, ATTENDEE_COMBO_READY_JS, "참석자 검색 콤보박스", timeout=30000)


def _attendee_names(page) -> list[str]:
    """모달에 올라와 있는 참석자 이름."""
    return [n for n in (_eval(page, ATTENDEE_NAMES_JS) or []) if n]


def _remove_attendees(page, names: list[str]) -> None:
    """작업지에 없는 사람을 지운다. 체크박스로 고르고 툴바의 '제거'를 누른다."""
    for name in names:
        selector = _eval(page, CHECK_ATTENDEE_JS, name)
        if not selector:
            dump = _dump(page, "attendee-modal", DUMP_ATTENDEE_MODAL_JS)
            raise AttachError(
                f"'{name}' 행을 찾지 못했습니다"
                + (f" (참석자 화면: {dump})" if dump else "")
            )
        page.check(selector)
        page.wait_for_timeout(300)

    # 고르기 전에는 '제거'가 비활성이다. 눌리게 된 다음에 누른다.
    _wait_js(
        page,
        "(sel) => { const b = document.querySelector(sel); return !!b && !b.disabled; }",
        "'제거' 버튼",
        arg=ATTENDEE_REMOVE,
        timeout=10000,
    )
    page.click(ATTENDEE_REMOVE)
    page.wait_for_timeout(1000)

    left = _attendee_names(page)
    still = [n for n in names if n in left]
    if still:
        raise AttachError(f"지워지지 않았습니다: {', '.join(still)}")


def _sync_attendees(page, report_url: str, row: Row, queries: list[str]) -> int:
    """화면의 참석자를 작업지에 적힌 사람들과 맞춘다.

    작업지에 없는 사람은 지우고, 빠진 사람은 넣는다. 이름 형태가 달라도 같은
    사람으로 본다 - 작업지의 'kyungsik.oh' 와 화면의 'Oh Kyungsik' 은 같다.

    바꿀 것이 없으면 모달을 닫고 지나간다. 저장은 마지막에 한 번만 한다.
    중간에 실패하면 아무것도 저장되지 않으므로 다시 돌리면 처음부터 다시 한다.
    """
    if not queries:
        return 0
    count = _eval(page, ATTENDEE_COUNT_JS)
    if count is None:
        raise AttachError("참석자 버튼을 찾지 못했습니다")

    _open_attendee_modal(page, report_url, row)
    names = _attendee_names(page)
    if count and not names:
        # 있다는데 한 줄도 못 읽었다. 이 상태로 지우고 넣으면 안 된다.
        dump = _dump(page, "attendee-modal", DUMP_ATTENDEE_MODAL_JS)
        page.click(ATTENDEE_CANCEL)
        raise AttachError(
            f"참석자가 {count}명이라는데 목록을 읽지 못했습니다. 아무것도 바꾸지 않았습니다."
            + (f" (참석자 화면: {dump})" if dump else "")
        )

    extra = [n for n in names if not any(name_matches(q, n) for q in queries)]
    missing = [q for q in queries if not any(name_matches(q, n) for n in names)]
    if not extra and not missing:
        page.click(ATTENDEE_CANCEL)  # 이미 작업지대로다
        page.wait_for_timeout(500)
        return 0

    try:
        if extra:
            _remove_attendees(page, extra)
            print(f"     (작업지에 없는 참석자를 지웠습니다: {', '.join(extra)})")
        for query in missing:
            _pick_attendee(page, query)
    except AttachError:
        _dump(page, "attendee-modal", DUMP_ATTENDEE_MODAL_JS)
        _dump(page, "combos-attendee", DUMP_COMBOS_JS)
        raise

    page.click(ATTENDEE_SAVE)

    # 참석자 수가 작업지와 같아졌는지로 확인한다. 모달이 닫혔는지나 주소가
    # 바뀌었는지는 추측이었고, 원래 확인하려던 것은 실제로 반영됐는지다.
    try:
        _wait_js(
            page,
            "(want) => {" + ATTENDEE_COUNT_JS_BODY + " return n === want; }",
            f"참석자가 {len(queries)}명이 되는 것",
            arg=len(queries),
            timeout=30000,
        )
    except AttachError:
        actual = _eval(page, ATTENDEE_COUNT_JS)
        _dump(page, "attendee-modal", DUMP_ATTENDEE_MODAL_JS)
        raise AttachError(
            f"참석자를 {len(queries)}명으로 맞추려 했는데 화면에는 {actual}명입니다"
        )
    return len(missing) + len(extra)


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
    fields_changed = bool(done)

    # 숙박비 상세에는 비즈니스 목적 칸이 아예 없다(실측). 없는 칸을 못 찾았다고
    # 건 전체를 실패시키면 안 된다.
    if plan.purpose:
        wrote = _set_field(page, PURPOSE_FIELD, plan.purpose, "비즈니스 목적",
                           required=plan.lodging is None)
        if wrote:
            done.append("목적")
            fields_changed = True
        elif wrote is None:
            print("     (이 경비 유형에는 비즈니스 목적 칸이 없어서 건너뜁니다)")
    if plan.comment and _set_field(page, COMMENT_FIELD, plan.comment, "코멘트"):
        done.append("코멘트")
        fields_changed = True

    if plan.lodging:
        # 숙박비는 탭을 오가야 해서 저장 시점이 따로다. 안에서 저장까지 한다.
        # 여기까지 바꾼 것(유형·코멘트)도 같이 저장되도록 넘겨준다.
        done += _apply_lodging(page, plan, report_url, changed=fields_changed)

    # 참석자를 넣고 나서 한 번에 저장한다. 참석자가 빈 채로 저장하면 Concur가
    # '필수 정보가 누락되었습니다' 창을 띄우고, 그 창을 닫으면 리포트로 튕겨
    # 나간다. 넣을 것을 다 넣고 저장하면 그 창이 아예 안 뜬다.
    added = _sync_attendees(page, report_url, row, parse_attendees(plan.attendee))
    if added:
        done.append(f"참석자 {added}명")

    # 숙박비는 _apply_lodging 안에서 이미 저장했다. 나머지는 여기서 한 번 저장한다.
    # 참석자만 바뀐 경우는 모달의 저장으로 끝나서 따로 저장하지 않아도 된다.
    if fields_changed and not plan.lodging:
        _save_expense(page, row, report_url, reopen=False)

    return ", ".join(done) if done else "이미 되어 있음"


def _apply_lodging(page, plan: Plan, report_url: str, changed: bool = False) -> list[str]:
    """숙박비 상세와 항목별 명세를 채운다. 저장까지 여기서 한다.

    순서를 지켜야 한다. 날짜 범위를 먼저 넣고 저장해야 항목별 명세 표가 그
    날짜로 만들어진다. 실측: 범위를 2026-08-02 - 2026-08-07 로 넣으면 표는
    8/2부터 8/6까지 5행이 생겼다. 즉 행 하나가 하룻밤이고 퇴실일은 빠진다.

    changed는 이 함수에 오기 전에(유형·코멘트) 이미 바꾼 것이 있는지다.
    바뀐 것이 하나도 없으면 저장하지 않는다.
    """
    lodging = plan.lodging
    amounts = nightly_split(plan.row.amount or 0, lodging.nights)
    done = []

    _open_tab(page, TAB_DETAILS, "상세 정보")
    if _set_date_range(page, lodging.checkin, lodging.checkout):
        changed = True
        done.append(f"숙박 {_md(lodging.checkin)}~{_md(lodging.checkout)} ({lodging.nights}박)")

    if lodging.location and _pick_from_combo(page, HINT_LOCATION, lodging.location, "숙박 위치"):
        done.append("숙박 위치")
        changed = True
    if lodging.channel and _pick_from_combo(page, HINT_CHANNEL, lodging.channel, "Booking channel"):
        done.append("Booking channel")
        changed = True

    # 중간에 저장하지 않는다. 객실 요금이 비어 있는 채로 저장하면 '필수 정보가
    # 누락되었습니다' 창이 뜨고, 그 창을 닫으면 리포트로 튕겨 나간다. 넣을 것을
    # 다 넣고 한 번만 저장한다.
    _open_tab(page, TAB_ITEMIZATION, "항목별 명세")

    # 화면이 어떤 상태인지 먼저 읽는다. 비어 있으면 '항목별 명세 추가'를 눌러
    # 입력 폼을 띄우고, 이미 명세가 있으면 손대지 않는다.
    later = bool(parse_attendees(plan.attendee))  # 뒤에 참석자를 넣을 것이 있나
    locked = _itemization_ready(page)
    if locked:
        done.append(locked)
        # 바꾼 것이 없으면 저장할 것도 없다. 다시 돌릴 때 이미 다 되어 있는
        # 건에서, 저장할 이유가 없는 화면의 저장 버튼을 찾다가 실패했다.
        if changed:
            # 저장은 상세 정보 탭으로 돌아가서 한다. 항목별 명세 탭에는 '경비
            # 저장'이 화면 밖(-10001, -9893)에만 있고 보이는 것은 '항목별 명세
            # 저장'뿐인 화면이 있다(실측 2026-07-05 711,620원). 그 버튼을 누르면
            # 열려 있는 명세 입력 폼이 저장돼서, 우리가 넣지 않은 명세가 생긴다.
            _open_tab(page, TAB_DETAILS, "상세 정보")
            _save_expense(page, plan.row, report_url, reopen=later)
        return done

    if needs_recurrence(len(amounts), lambda: bool(_eval(page, COMBO_READY_JS, HINT_RECURRENCE))):
        _pick_from_combo(page, HINT_RECURRENCE, RECUR_DIFFERENT_DAILY, "반복")
        page.wait_for_timeout(800)
    else:
        print("     (1박이라 '반복' 칸이 없습니다. 바로 객실 요금을 채웁니다)")

    done.append(_fill_room_rates(page, amounts))

    # 이 탭의 저장 버튼은 '경비 저장'이 아니라 '항목별 명세 저장'이다(실측:
    # data-nuiexp="itm-save-itemization"). 방금 채운 명세를 저장하는 것이므로
    # 이 버튼이 맞다. 저장하면 상세로 돌아온다.
    _save_expense(page, plan.row, report_url, SAVE_ITEMIZATION, reopen=later)
    return done


def _attendee_for(cfg: dict, entry, label: str) -> str:
    """작업지의 '참석자' + '추가 참석자'. 식음료 행에서만 쓴다.

    참석자 칸은 수식이라 본인이 자동으로 들어가고, 같이 드신 분은 옆의 추가
    참석자 칸에 적는다. 둘을 합쳐서 순서대로 넣고 중복은 뺀다.

    참석자 칸을 지우면 본인을 빼겠다는 뜻이다. 그때는 추가 참석자만 넣는다.
    다만 둘 다 비어 있으면 설정에 적어둔 사람을 쓴다 - 아무도 안 넣으면
    식음료는 필수값이 비어서 Concur가 리포트를 안 받는다. 엑셀을 한 번도
    열지 않아 수식이 계산되지 않은 경우도 여기에 걸린다.

    참석자 칸은 식음료 유형에만 있다. 주차비 같은 행에 넣으려 하면 그 버튼이
    없어 실패하므로 유형을 보고 거른다.
    """
    if LABEL_MEAL not in (entry.type_name or label or ""):
        return ""
    mine = parse_attendees(entry.attendee)
    others = parse_attendees(getattr(entry, "extra_attendee", ""))
    if not mine and not others:
        mine = parse_attendees(cfg.get("attendee_default", ""))
    return ", ".join(dict.fromkeys(mine + others))  # 순서 유지, 중복 제거


def _gaps(entry, label: str) -> list[str]:
    """작업지에 빠진 값. 채우지 않고 지나가면 사람이 알아채기 어렵다."""
    holes = []
    if LABEL_LODGING in (label or ""):
        if not (entry.checkin and entry.checkout):
            holes.append("입실·퇴실 날짜")
        if not entry.location:
            holes.append("숙박 위치")
        if not entry.channel:
            holes.append("Booking channel")
    elif LABEL_MEAL in (label or "") and not entry.attendee:
        holes.append("참석자")
    return holes


def _gaps_with_defaults(cfg: dict, entry, label: str) -> list[str]:
    """설정으로 채워지는 것은 빠진 값이 아니다.

    참석자는 작업지 수식이나 설정으로 자동으로 들어간다. 사람이 채워야 하는
    것은 '추가 참석자'인데 그건 없어도 되는 값이라 여기서 묻지 않는다.
    """
    holes = _gaps(entry, label)
    if "참석자" in holes and cfg.get("attendee_default"):
        holes.remove("참석자")
    return holes


def plans_from_sheet(cfg: dict, rows: list[Row], sheet_path: Path, tolerance: int):
    """작업지에 적힌 대로 계획을 만든다. 규칙 대신 사람이 정한 값을 쓴다.

    (계획, 작업지에 빠진 값, Concur에서 못 찾은 것) 세 가지를 준다.
    """
    entries = sheet.load(sheet_path)
    pairs, missing = match_rows(entries, rows, tolerance)
    plans, gaps = [], []
    for entry, row, how in pairs:
        code, label = None, row.expense_type
        # 화면 유형과 정확히 같을 때만 넘어간다. 예전에는 부분 일치로 봤는데
        # 다른 유형에 이름이 섞여 있으면 이미 바꾼 줄 알고 지나쳤다.
        if entry.type_name and entry.type_name != (row.expense_type or "").strip():
            code, label = settings.code_for(cfg, entry.type_name), entry.type_name
        lodging = None
        if entry.checkin and entry.checkout:
            # 작업지의 수식이 계산되지 않은 채 저장되면 빈 칸으로 읽힌다.
            # 그때는 설정의 기본값을 쓴다 - 엑셀이 보여주던 값과 같다.
            lodging = Lodging(
                entry.checkin,
                entry.checkout,
                entry.location or cfg.get("lodging_location_default", ""),
                entry.channel or cfg.get("booking_channel_default", ""),
            )
        plan = Plan(row, code, label, entry.purpose, entry.comment,
                    _attendee_for(cfg, entry, label), lodging)

        # 숙박비인데 날짜가 없으면 상세를 못 채운다. 코멘트만 넣고 지나가면
        # 다 된 것처럼 보이므로 여기서 짚어준다.
        holes = _gaps_with_defaults(cfg, entry, entry.type_name or label)
        if holes:
            gaps.append((entry, holes))
        if plan.type_code or plan.fill_meal or plan.lodging:
            # entry를 같이 준다. 어느 전표가 이 경비에 짝지어졌는지 보여줘야
            # 유형이 이상할 때 작업지가 틀린 건지 짝이 틀린 건지 알 수 있다.
            plans.append((plan, how, entry))
    return plans, gaps, missing


def rows_ready(rows: list[Row]) -> bool:
    """짝을 지을 만큼 읽혔는가.

    행 껍데기는 먼저 그려지고 날짜·금액은 조금 뒤에 채워진다. 그 사이에 읽으면
    행 수는 맞는데 값이 비어서, 작업지의 모든 줄이 '후보 없음'이 된다 - 짝은
    날짜와 금액으로만 짓기 때문이다. 실측(2026-08-11): 같은 리포트를 두 번째
    돌렸을 때 3건을 읽고도 2건 다 못 찾았다.
    """
    return bool(rows) and any(r.when and r.amount for r in rows)


def _rows_when_ready(page, tries: int = 10, wait_ms: int = 1000) -> list[Row]:
    """값이 채워질 때까지 다시 읽는다. 끝내 안 채워지면 읽힌 것을 그대로 준다."""
    rows: list[Row] = []
    for attempt in range(tries):
        rows = read_rows(page)
        if rows_ready(rows):
            return rows
        if attempt == 0:
            print("  (경비 목록이 아직 그려지는 중입니다. 기다립니다)")
        page.wait_for_timeout(wait_ms)
    return rows


def fix_phase(page, report_url: str, cfg: dict, apply: bool,
              limit: int | None, sheet_path: Path) -> int:
    """작업지에 적힌 대로 유형·목적·코멘트·참석자·숙박 상세를 채운다."""
    rows = _rows_when_ready(page)
    usable = [r for r in rows if r.expense_id and r.when and r.amount]
    if len(usable) != len(rows):
        print(f"  알림: 경비 {len(rows)}건 중 {len(rows) - len(usable)}건은 날짜·금액을 "
              "읽지 못해 짝짓기에서 제외했습니다")
        print_unreadable(rows)
        dump = dump_rows(page)
        if dump:
            print(f"  (행 마크업을 {dump} 에 남겼습니다)")
    paired, gaps, missing = plans_from_sheet(cfg, [r for r in rows if r.expense_id],
                                             sheet_path, int(cfg["date_tolerance_days"]))
    plans = [p for p, _, _ in paired]
    source = {id(p): (entry, how) for p, how, entry in paired}
    if gaps:
        print(f"\n작업지에 빠진 값이 있습니다 {len(gaps)}건. 그 부분은 채우지 못합니다:")
        for entry, holes in gaps:
            print(f"  {entry.when} {entry.amount:>9,}원  [{entry.type_name}] "
                  f"-> {', '.join(holes)} 가 비어 있습니다")
    if missing:
        print(f"\n작업지에는 있으나 Concur에서 찾지 못한 것 {len(missing)}건:")
        for entry, why in missing:
            print(f"  {entry.when} {entry.amount:>9,}원  {entry.merchant[:16]} - {why}")
        # 무엇과 견주다 못 찾았는지 같이 보여준다. '후보 없음'만 있으면
        # 작업지가 틀렸는지 화면이 다른지 알 길이 없다. 짝은 날짜(±허용일)와
        # 금액이 둘 다 맞아야 지어진다 - 여기 늘어놓으면 어디가 어긋났는지
        # 눈으로 바로 보인다.
        print(f"  견준 화면의 경비 {len(rows)}건:")
        for r in rows:
            print(f"    {r.when} {(r.amount or 0):>9,}원  {(r.vendor or '')[:16]}")

    print(f"\n경비 {len(rows)}건 중 {len(plans)}건을 수정합니다")
    print("  (화면의 현재 유형  ->  작업지대로 바꿀 내용  [짝지은 전표])\n")
    for plan in plans:
        r = plan.row
        entry, how = source.get(id(plan), (None, ""))
        mark = ""
        if entry is not None:
            mark = f"   [{entry.when} {entry.merchant[:12]}"
            mark += f", {how} 배정]" if how != "단독" else "]"
        print(f"  {r.when} {r.amount:>9,}원  {r.expense_type[:22]:22} -> {plan.summary()}{mark}")

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
    print("=" * 64)
    console.wait_enter("숙박위치와 Booking channel 드롭다운이 보이면")

    found = {}
    for key, hint, what in (
        ("lodging_locations", HINT_LOCATION, "숙박 위치"),
        ("booking_channels", HINT_CHANNEL, "Booking channel"),
    ):
        try:
            _wait_js(page, COMBO_READY_JS, f"{what} 콤보박스", arg=hint, timeout=15000)
            _click_marked(page, SELECT_COMBO_JS, f"{what} 콤보박스", arg=hint)
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


def _default_sheet() -> Path:
    """전표 폴더의 작업지. xlsx를 csv보다 먼저 본다."""
    folder = paths.folder(settings.load()["downloads_dir"])
    for name in ("manifest.xlsx", "manifest.csv"):
        if (folder / name).exists():
            return folder / name
    return folder / "manifest.csv"


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
                    help="작업지 경로입니다. 없으면 전표 폴더에서 찾습니다")
    ap.add_argument("--list-types", action="store_true",
                    help="화면의 경비유형과 코드를 뽑습니다 (새 유형이 생겼을 때 쓰세요)")
    ap.add_argument("--list-lodging", action="store_true",
                    help="숙박위치·Booking Channel 목록을 뽑아 settings.json 에 넣습니다")
    args = ap.parse_args()
    try:
        path = None
        if args.sheet is not None:
            path = Path(args.sheet) if args.sheet else _default_sheet()
        elif not (args.list_types or args.list_lodging):
            path = _default_sheet()
        return run(args.apply, args.limit, args.list_types, path, args.list_lodging)
    except (AttachError, sheet.SheetError) as exc:
        print(f"\n작업을 중단했습니다: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
