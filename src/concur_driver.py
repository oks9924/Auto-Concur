"""기존 Concur 조작을 보존하는 어댑터. 결과 확인은 새로 연 화면에서 수행한다."""
import re
from decimal import Decimal, InvalidOperation

from . import attach_receipts as ar, fix_expenses as fx, concur_ui as ui
from . import concur_graphql as gq
from .report_session import check_context
from .concur_formats import amount_check_js, range_matches
from .concur_values import parse_amount, same_text


class Driver:
    def __init__(self, page, report_url, folder):
        self.page, self.report_url, self.folder = page, report_url, folder
        self.matching = None

    def guard(self):
        check_context(self.page, self.report_url)

    def rows(self):
        self.guard()
        self.page.goto(self.report_url, wait_until='domcontentloaded')
        self.guard()
        return ar.rows_when_ready(self.page, allow_incomplete=True)

    def check_binding(self, intended, rows):
        if self.matching is not None and not self.matching.verify(intended, rows):
            raise ar.AttachError('보류: 현재 목록에서 대상 경비를 유일하게 확인하지 못했습니다.')

    def receipt_present(self, row, slip, again=False):
        current = self.rows()
        self.check_binding(row, current)
        matches = [r for r in current if r.expense_id == row.expense_id]
        found = matches[0] if len(matches) == 1 else None
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
        if not gq.disabled():
            try:
                gq.attach_receipt(self.page, row.expense_id, slip.path)
                print('        → GraphQL 첨부')
                return
            except gq.NotSent as why:
                # 아직 아무것도 경비에 붙지 않았다. 화면 경로로 간다.
                print(f'        → 화면으로 붙입니다 (GraphQL 못 씀: {why})')
        self._attach_by_screen(slip, row)

    def _attach_by_screen(self, slip, row):
        ar.open_expense(self.page, slip, row, self.report_url, self.folder)
        self.page.set_input_files(ar.UPLOAD_INPUT, str(slip.path))
        # 업로드가 저장 가능한 상태가 될 때까지 공통 클릭 준비 로직이 기다린다.
        self.page.wait_for_timeout(3000)
        fx._save_expense(self.page, row, self.report_url, reopen=False)

    # 한 번의 updateExpense 로 보낼 수 있는 계획인지 본다.
    #
    # 유형 변경이 여기 들어온다: `fields.expenseTypeId` 하나면 되고 policyId 는
    # 필요 없다 - 그걸 쓰던 `CDS_addRecentExpenseTypes` 를 우리 문서에서 뺐다.
    # 저장소가 이미 들고 있는 코드(`PARKG`·`01143`·`LODNG`)가 기록의
    # `expenseTypeId` 모양(digits / 1-8자)과 같다. 그리고 저장 뒤 `verify_edit`
    # 가 화면에서 유형을 다시 읽어 대조하므로 독립 확인이 남아 있다.
    #
    # 참석자는 별도 mutation 에 참석자별 versionNumber 와 금액 분배가 필요하고,
    # 숙박 명세는 roomRateExpenseTypeId 가 필요하다. 둘 다 아직 근거가 없어서
    # 화면 경로를 그대로 쓴다.
    @staticmethod
    def _fields_only(plan):
        if plan.lodging:
            return None
        fields = {}
        if plan.type_code:
            fields['expenseTypeId'] = plan.type_code
        if plan.purpose:
            fields['businessPurpose'] = plan.purpose
        if plan.comment:
            fields['comment'] = plan.comment
        return fields or None

    def _attendees_by_api(self, plan):
        """참석자를 GraphQL로 저장한다. 했으면 알림 문구, 못 하면 None.

        **한 명이라도 유일하게 못 고르면 아무것도 보내지 않는다.**
        `saveAttendeesLegacy` 는 목록을 통째로 교체해서, 반쯤 고른 목록을
        보내면 나머지가 지워진다 - 전에 실제로 낸 사고가 그 모양이었다.
        """
        from .sheet import nightly_split
        wanted = fx.parse_attendees(plan.attendee)
        if not wanted:
            return None
        _, user_id, _ = gq.ids(self.page)
        keys = gq.attendee_type_keys(self.page, user_id)
        chosen = []
        for query in wanted:
            found = gq.search_attendees(self.page, query, keys)
            one = gq.pick_attendee(query, found)
            if one is None:
                raise gq.NotSent(f'참석자 {query!r} 를 후보 {len(found)}명 중 '
                                 '유일하게 고르지 못했습니다')
            if any(x['atnKey'] == one['atnKey'] for x in chosen):
                continue
            chosen.append(one)
        amounts = nightly_split(plan.row.amount or 0, len(chosen))
        gq.save_attendees(self.page, plan.row.expense_id, chosen, amounts)
        return f'참석자 {len(chosen)}명'

    def apply_edit(self, plan):
        self.guard()
        fields = None if gq.disabled() else self._fields_only(plan)
        if fields or (plan.attendee and not plan.lodging and not gq.disabled()):
            try:
                saved = (gq.save_fields(self.page, plan.row.expense_id, fields)
                         if fields else gq.Saved(plan.row.expense_id, False, (), ()))
                who = self._attendees_by_api(plan) if plan.attendee else None
                shown = dict(fields or {})
                if 'expenseTypeId' in shown:
                    shown['expenseTypeId'] = plan.type_label or plan.type_code
                parts = [f'{k}->{v[:20]}' for k, v in shown.items()]
                if who:
                    parts.append(who)
                done = ', '.join(parts)
                print('        → GraphQL 저장 · ' + done)
                if saved.blocking or saved.messages:
                    # 저장 응답이 예외를 같이 준다. 화면 글자를 읽지 않아도 안다.
                    note = '; '.join(saved.messages[:3]) or '차단 예외'
                    if saved.missing:
                        note += f" (빈 필드: {', '.join(saved.missing[:5])})"
                    return f'{done} · Concur 예외: {note}'
                return done
            except gq.NotSent as why:
                print(f'        → 화면으로 저장합니다 (GraphQL 못 씀: {why})')
        return fx.apply_plan(self.page, plan, self.report_url)

    def open_for_verification(self, row):
        self.page.goto(ar.expense_url(self.report_url, row.expense_id), wait_until='domcontentloaded')
        self.guard()
        ui.wait_condition(self.page, amount_check_js(), '저장된 경비 금액 확인', str(row.amount))

    def verify_edit(self, plan):
        page = self.page
        self.guard()
        if self.matching is not None:
            self.check_binding(plan.row, self.rows())
        self.open_for_verification(plan.row)
        checks = []
        for selector, expected in ((fx.PURPOSE_FIELD, plan.purpose), (fx.COMMENT_FIELD, plan.comment)):
            if expected:
                actual = page.evaluate('(s) => document.querySelector(s)?.value ?? null', selector)
                checks.append(None if actual is None else same_text(actual, expected))
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
                    checks.append(None if actual is None else same_text(actual, expected))
            if lodging.nights:
                value = page.evaluate('(s) => document.querySelector(s)?.value ?? null', fx.DATE_RANGE_FIELD)
                checks.append(None if value is None else range_matches(value, lodging.checkin, lodging.checkout, page.get_attribute(fx.DATE_RANGE_FIELD, 'placeholder') or ''))
        if plan.attendee:
            page.goto(ar.expense_url(self.report_url, plan.row.expense_id) + '?modal=attendees&context=entry',
                      wait_until='domcontentloaded')
            ui.wait_condition(page, fx.ATTENDEE_COMBO_READY_JS, '저장된 참석자 조회')
            names, wanted = fx._attendee_names(page), fx.parse_attendees(plan.attendee)
            checks.append(bool(names) and len(names) == len(wanted) and
                          all(any(fx.name_matches(q, n) for n in names) for q in wanted))
            page.goto(ar.expense_url(self.report_url, plan.row.expense_id), wait_until='domcontentloaded')
            ui.wait_condition(page, amount_check_js(), '경비 상세 복귀', str(plan.row.amount))
        if lodging and lodging.nights:
            fx._open_tab(page, fx.TAB_ITEMIZATION, '저장된 명세 조회')
            state = page.evaluate(fx.ITEMIZATION_STATE_JS)
            rates = page.evaluate(fx.ROOM_RATE_INPUTS_JS)
            if state.get('empty'):
                checks.append(False)
            elif rates:
                values = [parse_amount(r.get('value')) for r in rates]
                checks.append(None if any(value is None for value in values) else
                              values == fx.nightly_split(plan.row.amount, lodging.nights))
            else:
                # 기존 명세 표를 수치로 읽을 근거가 없으면 완료로 오인하지 않는다.
                checks.append(None)
        if any(value is False for value in checks):
            return False
        return None if any(value is None for value in checks) else True
