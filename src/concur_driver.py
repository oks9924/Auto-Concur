"""기존 Concur 조작을 보존하는 어댑터. 결과 확인은 새로 연 화면에서 수행한다."""
import re
from decimal import Decimal, InvalidOperation

from . import attach_receipts as ar, fix_expenses as fx, concur_ui as ui
from .report_session import check_context


class Driver:
    def __init__(self, page, report_url, folder):
        self.page, self.report_url, self.folder = page, report_url, folder

    def guard(self):
        check_context(self.page, self.report_url)

    def rows(self):
        self.guard()
        self.page.goto(self.report_url, wait_until='domcontentloaded')
        self.guard()
        return ar.rows_when_ready(self.page)

    def receipt_present(self, row, slip, again=False):
        found = next((r for r in self.rows() if r.expense_id == row.expense_id), None)
        if not found or found.when != row.when or found.amount != row.amount:
            raise ar.AttachError('전표와 경비의 식별 정보가 달라졌습니다.')
        if found.has_receipt is None:
            return None
        if found.has_receipt and found.receipt_file and found.receipt_file != slip.path.name:
            return None
        if found.has_receipt and not found.receipt_file:
            return None
        return found.has_receipt

    def attach(self, slip, row):
        self.guard()
        ar.open_expense(self.page, slip, row, self.report_url, self.folder)
        self.page.set_input_files(ar.UPLOAD_INPUT, str(slip.path))
        # 업로드가 저장 가능한 상태가 될 때까지 공통 클릭 준비 로직이 기다린다.
        self.page.wait_for_timeout(3000)
        fx._save_expense(self.page, row, self.report_url, reopen=False)

    def apply_edit(self, plan):
        self.guard()
        return fx.apply_plan(self.page, plan, self.report_url)

    def verify_edit(self, plan):
        page = self.page
        self.guard()
        page.goto(ar.expense_url(self.report_url, plan.row.expense_id), wait_until='domcontentloaded')
        self.guard()
        ui.wait_condition(page, ar.WAIT_AMOUNT_JS, '저장된 경비 금액 확인', str(plan.row.amount))
        checks = []
        for selector, expected in ((fx.PURPOSE_FIELD, plan.purpose), (fx.COMMENT_FIELD, plan.comment)):
            if expected:
                actual = page.evaluate('(s) => document.querySelector(s)?.value ?? null', selector)
                checks.append(None if actual is None else actual.strip() == expected.strip())
        if plan.type_code:
            # 저장 후 선택된 이름을 해당 코드의 실제 옵션 이름과 비교한다.
            actual = page.evaluate('() => {' + fx.FIND_COMBO_FN +
                " return findCombo(/Expense Type|경비 유형/)?.innerText?.trim() || null; }")
            ui.click_target(page, fx.SELECT_TYPE_COMBO_JS, '유형 확인')
            try:
                expected = ui.wait_condition(page, '''(code) => [...document.querySelectorAll('li[role="option"]')]
                  .find(o => (o.id || '').includes('-_-_-' + code + '-_-_-'))?.innerText?.trim()''',
                  '저장된 유형 코드 확인', plan.type_code, timeout=10000)
                checks.append(bool(actual and expected and expected in actual))
            finally:
                page.keyboard.press('Escape')
        lodging = plan.lodging
        if lodging:
            for hint, expected in ((fx.HINT_LOCATION, lodging.location), (fx.HINT_CHANNEL, lodging.channel)):
                if expected:
                    actual = page.evaluate(fx.COMBO_VALUE_JS, hint)
                    checks.append(None if actual is None else actual.strip() == expected.strip())
            if lodging.nights:
                value = page.evaluate('(s) => document.querySelector(s)?.value ?? null', fx.DATE_RANGE_FIELD)
                checks.append(None if value is None else all(d.isoformat() in value for d in (lodging.checkin, lodging.checkout)))
        if plan.attendee:
            page.goto(ar.expense_url(self.report_url, plan.row.expense_id) + '?modal=attendees&context=entry',
                      wait_until='domcontentloaded')
            ui.wait_condition(page, fx.ATTENDEE_COMBO_READY_JS, '저장된 참석자 조회')
            names, wanted = fx._attendee_names(page), fx.parse_attendees(plan.attendee)
            checks.append(bool(names) and len(names) == len(wanted) and
                          all(any(fx.name_matches(q, n) for n in names) for q in wanted))
            page.goto(ar.expense_url(self.report_url, plan.row.expense_id), wait_until='domcontentloaded')
            ui.wait_condition(page, ar.WAIT_AMOUNT_JS, '경비 상세 복귀', str(plan.row.amount))
        if lodging and lodging.nights:
            fx._open_tab(page, fx.TAB_ITEMIZATION, '저장된 명세 조회')
            state = page.evaluate(fx.ITEMIZATION_STATE_JS)
            rates = page.evaluate(fx.ROOM_RATE_INPUTS_JS)
            if state.get('empty'):
                checks.append(False)
            elif rates:
                try:
                    values = [Decimal(re.sub(r'[^0-9.\-]', '', r['value']) or '0') for r in rates]
                    checks.append(values == fx.nightly_split(plan.row.amount, lodging.nights))
                except InvalidOperation:
                    checks.append(None)
            else:
                # 기존 명세 표를 수치로 읽을 근거가 없으면 완료로 오인하지 않는다.
                checks.append(None)
        if any(value is False for value in checks):
            return False
        return None if any(value is None for value in checks) else True
