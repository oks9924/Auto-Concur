"""Read one selected expense; unrelated list rows never decide its identity.

Selectors reuse fields used by the existing Concur adapter. Unknown or ambiguous
fields are reported for this expense, not filled from a guessed worksheet row.
"""
from dataclasses import replace
import re
import time
from urllib.parse import urlsplit

from . import attach_receipts as ar, fix_expenses as fx
from .concur_driver import Driver
from .concur_formats import placeholder_order
from .concur_values import clean, parse_amount, resolve_dates
from .report_session import check_context


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
  const day = one('input[id^="transactionDate"], input[name="transactionDate"]');
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
    vendorField: !!vendor
  };
}'''


def decode_detail(raw, seed):
    order = placeholder_order(raw.get('placeholder', ''))
    when = resolve_dates([raw], order=order)[0]
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
    if not raw.get('dateField') or when is None:
        problems.append('상세 거래 날짜 미확인 (Concur 표시 서식 확인)')
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
