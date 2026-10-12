"""Read one selected expense; unrelated list rows never decide its identity.

Selectors reuse fields used by the existing Concur adapter. Unknown or ambiguous
fields are reported for this expense, not filled from a guessed worksheet row.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
import re
import time
from urllib.parse import urlsplit

from . import attach_receipts as ar, fix_expenses as fx
from .concur_driver import Driver
from .concur_formats import placeholder_order
from .concur_values import clean, date_options, parse_amount, resolve_dates
from .report_session import check_context


# 이 계정이 날짜를 보여주는 순서. 한 번 확인되면 실행 내내 쓰고, 설정에도
# 남겨서 다음 실행은 처음부터 안 멈춘다. 서로 다른 순서가 관찰되면(있을 수
# 없는 일이다) 아무것도 믿지 않는다 - 'CONFLICT' 로 두고 사람에게 넘긴다.
_LEARNED = ContextVar('concur_learned_order', default=None)


# 이 실행에서 처리할 원본 거래의 날짜들. 모호한 표시를 가르는 근거로만 쓴다.
_KNOWN_DATES = ContextVar('concur_known_dates', default=())


@contextmanager
def using_known_dates(dates):
    token = _KNOWN_DATES.set(tuple(sorted({d for d in dates if d})))
    try:
        yield
    finally:
        _KNOWN_DATES.reset(token)


def _slip_match(options, tolerance=1):
    """후보 중 원본 거래 날짜와 맞는 것이 하나뿐이면 그것이다.

    리포트는 한 달치만 올린다. '07/09/2026' 의 두 해석(7월 9일 / 9월 7일) 중
    9월 전표만 있는 리포트에서 7월은 있을 수 없다.

    둘 다 맞으면 고르지 않는다 - 그때는 날짜로 가릴 수 없다는 뜻이고, 여기서
    아무거나 고르면 엉뚱한 경비에 영수증이 붙는다. 금액까지 맞아야 짝이
    지어지지만, 그 판단을 이 함수가 미리 해버리면 안 된다.
    """
    known = _KNOWN_DATES.get()
    if not known:
        return None
    hits = [when for when in options
            if any(abs((when - day).days) <= tolerance for day in known)]
    return hits[0] if len(hits) == 1 else None


def learned_order():
    found = _LEARNED.get()
    return found if found in ('MDY', 'DMY') else None


def _remember(order):
    if order not in ('MDY', 'DMY'):
        return
    found = _LEARNED.get()
    if found == order:
        return
    _LEARNED.set('CONFLICT' if found else order)
    if found is None:
        _save_order(order)


def _remember_from(raw):
    for order in date_options(clean(raw.get('date', ''))).values():
        _remember(order)


def _save_order(order):
    """확인된 순서를 설정에 남긴다. 사람이 고르던 것을 대신 채워준다."""
    from . import settings
    try:
        cfg = settings.load()
        if str(cfg.get('concur_date_order', 'AUTO')).upper() != 'AUTO':
            return  # 사람이 정해둔 값은 건드리지 않는다
        settings.save({**cfg, 'concur_date_order': order})
        print(f'  (이 계정의 날짜 표시 순서를 {ORDER_NAMES[order]} 로 확인해 기억합니다)')
    except Exception:
        pass  # 못 남겨도 이번 실행은 그대로 간다


class ContextChanged(ar.AttachError):
    """A different report/login is a run-level stop, not an expense-level skip."""


def guard_report(page, report_url):
    try:
        check_context(page, report_url)
    except ar.AttachError as exc:
        raise ContextChanged(str(exc)) from exc


def guard_expense(page, report_url, expense_id):
    guard_report(page, report_url)
    expected = urlsplit(ar.expense_url(report_url, expense_id))
    current = urlsplit(page.url)
    if (current.netloc, current.path.rstrip('/')) != (expected.netloc, expected.path.rstrip('/')):
        raise ar.AttachError('선택한 경비 ID와 현재 상세 화면이 다릅니다.')


DETAIL_JS = '() => {' + fx.FIND_COMBO_FN + r'''
  const one = selector => {
    const nodes = [...surface().querySelectorAll(selector)].filter(visible);
    return nodes.length === 1 ? nodes[0] : null;
  };
  const value = el => el ? ('value' in el ? el.value : el.innerText || '').trim() : '';
  const DAY = 'input[id^="transactionDate"], input[name="transactionDate"]';
  const day = one(DAY);
  // 왜 못 읽었는지 가르기 위한 근거. one() 은 '보이는 것이 정확히 하나'일 때만
  // 값을 준다. 후보가 0개인 것과 2개 이상인 것은 원인이 다른데 메시지가 같았다.
  const dayPool = (root) => [...root.querySelectorAll(DAY)].map(el => ({
    id: el.id || el.getAttribute('name') || '', value: (el.value || '').trim(),
    shown: visible(el)
  }));
  const amount = one('#transactionAmount, input[name="transactionAmount"]');
  const vendor = one('#vendorName, input[name="vendorName"]');
  const currency = one('#transactionCurrencyName, [name="transactionCurrencyName"]');
  const kind = findCombo(/Expense Type|경비\s*유형/i);
  return {
    date: value(day), dateISO: day?.getAttribute('datetime') || '',
    dateLabel: day?.getAttribute('aria-label') || '',
    placeholder: day?.getAttribute('placeholder') || '',
    amount: value(amount), vendor: value(vendor), currency: value(currency),
    expenseType: value(kind), amountField: !!amount, dateField: !!day,
    vendorField: !!vendor,
    dateHere: dayPool(surface()), datePage: dayPool(document)
  };
}'''


ORDER_NAMES = {'DMY': '일/월/년', 'MDY': '월/일/년', 'YMD': '년/월/일'}


def _reading_hint(text):
    """어느 서식으로 읽으면 며칠이 되는지. 설정을 한 번에 고르게 해준다.

    '10/09/2026' 이 10월 9일인지 9월 10일인지는 화면만 봐서는 알 수 없다.
    둘 다 적어주면 전표 날짜와 맞는 쪽을 사람이 바로 고를 수 있다.
    """
    from .concur_values import date_options

    pairs = [(ORDER_NAMES[order], when)
             for when, order in sorted(date_options(text).items())
             if order in ORDER_NAMES]
    if len(pairs) < 2:
        return ''
    return '\n       ' + ', '.join(f'{name}이면 {when}' for name, when in pairs)


def _where(raw):
    """날짜 칸 후보를 한 줄로. 0개인지 여러 개인지가 원인을 가른다."""
    def shown(pool):
        return [f'{x["id"]}={x["value"]!r}' for x in pool if x.get('shown')]
    here, page = shown(raw.get('dateHere') or []), shown(raw.get('datePage') or [])
    if here:
        return f'(보이는 후보 {len(here)}개: {", ".join(here[:3])})'
    if page:
        return f'(상세 영역 밖에만 있습니다: {", ".join(page[:3])})'
    return '(이 화면에 거래일 입력칸이 없습니다)'


def _settle_date(raw, seed):
    """모호한 날짜를 근거로 가른다. 못 가리면 None - 지어내지 않는다.

    '10/09/2026' 은 10월 9일일 수도 9월 10일일 수도 있다. 화면 글자만으로는
    정할 수 없지만, 다음 둘은 추측이 아니라 근거다.

      1. 목록이 읽은 같은 경비의 날짜. 목록은 전체 행을 모아 읽어서 서식을
         스스로 추론한다(13일 이상인 날짜 한 건이면 순서가 정해진다). 상세는
         한 건씩 읽어서 그 추론이 안 걸렸다 - 여기서 이어준다.
      2. 이 계정에서 이미 확인된 순서. 한 계정이 날짜를 두 가지로 보여주지는
         않으므로, 같은 실행에서 모호하지 않게 읽힌 순서를 그대로 쓴다.

    가린 뒤에는 그 순서를 설정에 남긴다. 다음 실행은 처음부터 안 멈춘다.
    """
    from .concur_values import date_options

    options = date_options(clean(raw.get('date', '')))
    if len(options) < 2:
        return None

    if seed.when in options:  # 목록과 상세가 같은 날을 가리킨다
        _remember(options[seed.when])
        return seed.when

    known = learned_order()
    hits = [when for when, order in options.items() if order in (known, 'SAME')]
    if known and len(hits) == 1:
        return hits[0]

    settled = _slip_match(options)  # 원본 거래 날짜와 맞는 해석이 하나뿐인가
    if settled is not None:
        _remember(options[settled])
    return settled


def decode_detail(raw, seed):
    order = placeholder_order(raw.get('placeholder', ''))
    when = resolve_dates([raw], order=order)[0]
    if when is None:
        when = _settle_date(raw, seed)
    elif len(date_options(clean(raw.get('date', '')))) < 2:
        _remember_from(raw)  # 모호하지 않게 읽혔다. 이 순서를 기억해 둔다
    amount = parse_amount(raw.get('amount', ''))
    # Explicit non-KRW currencies must never match a KRW amount with equal digits.
    currency = clean(raw.get('currency', ''))
    codes = re.findall(r'\b[A-Z]{3}\b', currency.upper())
    if codes and any(code != 'KRW' for code in codes):
        amount = None
    if any(symbol in currency for symbol in '$€£¥'):
        amount = None
    marked = currency + ' ' + clean(raw.get('amount', ''))
    if not (re.search(r'\bKRW\b', marked, re.I) or '₩' in marked or currency == '원'):
        amount = None  # no silent assumption that a currency-less number is KRW
    problems = []
    if not raw.get('dateField'):
        problems.append('상세 거래 날짜 입력칸을 고르지 못했습니다 ' + _where(raw))
    elif when is None:
        shown = clean(raw.get('date', ''))
        problems.append(
            f'상세 거래 날짜 {shown!r} 를 날짜로 읽지 못했습니다 '
            f'(칸 서식 {clean(raw.get("placeholder", ""))!r}, Concur 표시 서식 확인)'
            + _reading_hint(shown))
    if not raw.get('amountField') or amount is None:
        problems.append('상세 금액 또는 통화 미확인')
    if not raw.get('vendorField'):
        problems.append('상세 가맹점 입력칸 미확인')
    if problems:
        raise ar.AttachError(' / '.join(problems))
    return replace(seed, when=when, amount=amount, vendor=raw.get('vendor', ''),
                   expense_type=raw.get('expenseType') or seed.expense_type,
                   raw_date=raw.get('date', ''), raw_amount=raw.get('amount', ''),
                   read_problem='')


def read_detail(page, report_url, seed, timeout_ms=12000):
    """Navigate by ID and wait for stable detail values, not a list-wide match."""
    if not seed.expense_id or not re.fullmatch(r'[A-Za-z0-9_-]+', seed.expense_id):
        raise ar.AttachError('경비 ID가 없거나 상세 주소로 사용할 수 없습니다.')
    guard_report(page, report_url)
    page.goto(ar.expense_url(report_url, seed.expense_id), wait_until='domcontentloaded')
    deadline = time.monotonic() + timeout_ms / 1000
    previous, repeats, last = None, 0, '상세 입력칸 준비 대기'
    while time.monotonic() < deadline:
        guard_expense(page, report_url, seed.expense_id)
        raw = page.evaluate(DETAIL_JS)
        repeats = repeats + 1 if raw == previous else 0
        previous = raw
        try:
            result = decode_detail(raw, seed)
        except ar.AttachError as exc:
            last = str(exc)
        else:
            if repeats >= 1:
                guard_expense(page, report_url, seed.expense_id)
                return result
        page.wait_for_timeout(150)
    raise ar.AttachError(last)


def same_identity(a, b):
    return (a.expense_id == b.expense_id and a.when == b.when and a.amount == b.amount
            and clean(a.vendor).casefold() == clean(b.vendor).casefold())


class DetailDriver(Driver):
    """Keep the existing field writer but verify only the current expense."""
    def guard(self):
        guard_report(self.page, self.report_url)

    def inspect(self, row):
        return read_detail(self.page, self.report_url, row)

    def open_for_verification(self, row):
        current = self.inspect(row)
        if not same_identity(row, current):
            raise ar.AttachError('상세 날짜·금액·가맹점이 변경되어 이 경비를 보류합니다.')
        return current

    def receipt_row(self, row, tries=30):
        """The list supplies receipt state for ONE ID, not matching candidates.

        A partially unreadable unrelated row is irrelevant here. The selected
        row still needs stable receipt evidence; loading is never treated as none.
        """
        self.guard()
        self.page.goto(self.report_url, wait_until='domcontentloaded')
        previous, repeats = None, 0
        for _ in range(tries):
            self.guard()
            matches = [r for r in ar.read_rows(self.page) if r.expense_id == row.expense_id]
            if len(matches) == 1 and matches[0].read_problem != '목록 로딩 중':
                found = matches[0]
                signature = (found.expense_id, found.has_receipt, found.receipt_file)
                repeats = repeats + 1 if signature == previous else 0
                previous = signature
                if repeats >= 2:
                    return found
            else:
                previous, repeats = None, 0
            self.page.wait_for_timeout(150)
        raise ar.AttachError('해당 경비의 현재 영수증 상태를 확인하지 못했습니다.')

    def receipt_present(self, row, slip, again=False):
        # Existing receipt means preserve, even when its filename differs.
        # This verifier is used for an upload task: post-upload still checks name.
        self.open_for_verification(row)
        found = self.receipt_row(row)
        if found.has_receipt is not True:
            return found.has_receipt
        return True if found.receipt_file == slip.path.name else None

    def attach(self, slip, row):
        found = self.receipt_row(row)
        if found.has_receipt is not False:
            raise ar.AttachError('영수증이 이미 있거나 상태가 불명확해 추가 업로드하지 않았습니다.')
        self.open_for_verification(row)
        # Do not call the legacy helper, which navigates again after verification.
        self.page.set_input_files(ar.UPLOAD_INPUT, str(slip.path))
        self.page.wait_for_timeout(3000)
        guard_expense(self.page, self.report_url, row.expense_id)
        fx._save_expense(self.page, row, self.report_url, reopen=False)

    def apply_edit(self, plan):
        self.open_for_verification(plan.row)
        return fx.apply_plan(self.page, plan, self.report_url, opened=True)

    def verify_edit(self, plan):
        # The base implementation calls our identity-verifying navigation hook.
        return super().verify_edit(plan)
