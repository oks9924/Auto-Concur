"""개선판 회귀 테스트. 실제 서비스에 로그인하거나 데이터를 전송하지 않는다."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
import os
import tkinter as tk
import pytest
from playwright.sync_api import sync_playwright, Error
from src import attach_receipts as ar
from src.concur_values import date_options, resolve_dates, parse_amount, amount_from_summary
from src.calendar_input import DatePicker, DateEntry, initial_period, checked_period


@pytest.mark.parametrize('text', ['2026-09-17', '2026. 9. 17.', '2026/09/17',
    '2026년 9월 17일', '2026. 09. 17. (목)', '20260917', 'Sep 17, 2026', '17 September 2026',
    '\u200e2026-09-17\u200f'])
def test_screen_date_formats(text):
    assert set(date_options(text)) == {date(2026, 9, 17)}


@pytest.mark.parametrize('text', ['2026-02-30', '31/04/2026', '9/17', 'pending', '', ''])
def test_invalid_or_yearless_screen_dates_are_not_guessed(text):
    assert not date_options(text)


def test_ambiguous_date_stays_unresolved():
    assert resolve_dates([{'date': '09/08/2026'}]) == [None]


@pytest.mark.parametrize('values', [(['09/17/2026', '09/08/2026']), (['17/09/2026', '08/09/2026'])])
def test_list_evidence_resolves_numeric_order(values):
    assert resolve_dates([{'date': v} for v in values]) == [date(2026, 9, 17), date(2026, 9, 8)]


def test_machine_date_resolves_display_without_locale_guess():
    assert resolve_dates([{'date': '09/08/2026', 'dateISO': '2026-09-08'}]) == [date(2026, 9, 8)]
    assert resolve_dates([{'date': '2026-09-08', 'dateISO': '2026-08-09'}]) == [None]


def test_conflicting_numeric_order_does_not_resolve_other_rows():
    assert resolve_dates([{'date': '09/17/2026'}, {'date': '18/09/2026'}, {'date': '09/08/2026'}])[-1] is None


@pytest.mark.parametrize('text,value', [('KRW\xa017,000.00',17000), ('₩ 17,000',17000),
    ('17 000 원',17000), ('(1,000)',-1000), ('−1,000',-1000), ('0',0), (0,0)])
def test_krw_amounts(text, value):
    assert parse_amount(text) == value


@pytest.mark.parametrize('text', ['USD 17,000', '$17,000', 'EUR 1,000', '17,00', '17.5', '17.000', '17O00'])
def test_unsupported_currency_or_number_not_rounded(text):
    assert parse_amount(text) is None


def test_summary_amount():
    assert amount_from_summary('경비 KRW 17,000 날짜 2026-09-17 선택') == 17000
    assert amount_from_summary('KRW 1,000 KRW 2,000') is None


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(headless=True, executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        except Error as exc:
            pytest.skip(str(exc))
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def row_html(day='2026. 09. 17.', amount='KRW 17,000', rowid='E1', style=''):
    return f'''<div role="row" data-testid="data-row" id="{rowid}" style="{style}">
    <span data-nuiexp="date-cell">{day}</span><span data-nuiexp="amount-cell">{amount}</span>
    <span data-nuiexp="expense-type-cell">숙박비</span><span data-nuiexp="receipts-cell"></span></div>'''


def test_dotted_korean_date_and_krw_can_be_read(page):
    page.set_content(row_html())
    rows = ar.rows_when_ready(page, tries=2, wait_ms=1)
    assert rows[0].when == date(2026,9,17) and rows[0].amount == 17000


def test_hidden_templates_not_counted_but_hidden_cell_text_is_read(page):
    page.set_content(row_html(style='display:none') + row_html(rowid='E2').replace(
        '<span data-nuiexp="date-cell">', '<span style="display:none" data-nuiexp="date-cell">'))
    rows = ar.rows_when_ready(page, tries=2, wait_ms=1)
    assert len(rows) == 1 and rows[0].expense_id == 'E2'


def test_real_headers_fallback_when_field_hooks_change(page):
    page.set_content('''<div role="grid"><div role="row"><div role="columnheader" aria-colindex="1">날짜</div>
    <div role="columnheader" aria-colindex="2">요청됨</div></div>
    <div role="row" data-testid="data-row" id="E1"><div role="cell" aria-colindex="1">2026. 9. 17.</div>
    <div role="cell" aria-colindex="2">KRW 17,000</div></div></div>''')
    rows = ar.rows_when_ready(page, tries=2, wait_ms=1)
    assert rows[0].when == date(2026,9,17) and rows[0].amount == 17000


def test_zero_amount_not_replaced_by_fallback(page):
    page.set_content(row_html(amount='0'))
    assert ar.rows_when_ready(page, tries=2, wait_ms=1)[0].amount == 0


@pytest.mark.parametrize('html', [row_html(day='09/08/2026'), row_html(rowid='E1')+row_html(rowid='E1'),
    '<div role="grid" aria-busy="true">'+row_html()+'</div>',
    '<div role="grid" aria-rowcount="4">'+row_html()+'</div>'])
def test_ambiguous_duplicate_busy_partial_lists_still_block(page, html, monkeypatch):
    page.set_content(html)
    monkeypatch.setattr(ar.concur_ui, 'diagnose', lambda *a: '')
    with pytest.raises(ar.AttachError, match='일부 행만으로'):
        ar.rows_when_ready(page, tries=2, wait_ms=1)


def test_delayed_rows_wait_without_partial_matching(page):
    page.set_content(row_html(day='') + '''<script>setTimeout(()=>{
    document.querySelector('[data-nuiexp="date-cell"]').textContent='2026. 9. 17.';}, 80)</script>''')
    assert ar.rows_when_ready(page, tries=20, wait_ms=20)[0].when == date(2026,9,17)


def test_period_defaults_and_saved_range():
    assert initial_period({}, date(2026,9,17)) == ('2026.09.01','2026.09.17')
    assert initial_period({'period_from':'2026.08.01','period_to':'2026.08.31'}) == ('2026.08.01','2026.08.31')
    assert checked_period('2026.09.17','2026.09.17') == ('2026.09.17','2026.09.17')
    with pytest.raises(ValueError):
        checked_period('2026.09.18','2026.09.17')


@pytest.fixture
def root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_calendar_selection_and_cancel(root):
    variable = tk.StringVar(root, value='2026.09.17')
    field = DateEntry(root, variable)
    field.open()
    field.dialog.move(1)
    field.dialog.select(date(2026,10,4))
    assert variable.get() == '2026.10.04'
    field.open()
    field.dialog.close()
    assert variable.get() == '2026.10.04'


def test_calendar_year_boundary_and_leap_day(root):
    result = []
    dialog = DatePicker(root, date(2026,12,31), result.append)
    dialog.move(1)
    assert (dialog.year, dialog.month) == (2027,1)
    dialog.select(date(2028,2,29))
    assert result == [date(2028,2,29)]


def test_calendar_restores_previous_modal_grab(root):
    window = tk.Toplevel(root)
    window.grab_set()
    dialog = DatePicker(window, date(2026,9,17), lambda value: None)
    dialog.close()
    assert root.grab_current() == window
    window.destroy()


def test_editor_enter_on_filtered_checkout_opens_right_calendar(root, tmp_path):
    from src import settings, organize
    from src.worksheet import write_json
    from src.worksheet_editor import Editor
    from src.stay_calendar import StayCalendar
    rows = [{**dict.fromkeys(organize.MANIFEST_COLUMNS,''), '거래일':'2026-09-17','금액':'1000',
             '승인번호':str(i),'파일명':f'{i}.pdf','가맹점명':f'가게 {i}'} for i in (1,2)]
    path = tmp_path/'workbook.json'
    write_json(path, {'version':1,'rows':rows})
    editor = Editor(root, path, settings.DEFAULTS)
    editor.query.set('가게 2')
    col = editor.COLUMNS.index('퇴실날짜')
    editor.table.select_cell(0,col)
    event = SimpleNamespace(row=0,column=col,key='Return',value='')
    assert editor.begin_cell_edit(event) is None
    root.update()
    popup = next(w for w in editor.winfo_children() if isinstance(w, StayCalendar))
    assert popup.mode.get() == '퇴실'
    popup.start, popup.end = date(2026,9,18), date(2026,9,20)
    popup.apply()
    editor.sync()
    assert editor.model.rows[0]['입실날짜'] == ''
    assert editor.model.rows[1]['입실날짜'] == '2026-09-18'
    assert editor.model.rows[1]['퇴실날짜'] == '2026-09-20'
    editor.table.undo()
    editor.sync()
    assert editor.model.rows[1]['입실날짜'] == ''
    if editor.pending:
        editor.after_cancel(editor.pending)
    editor.destroy()
