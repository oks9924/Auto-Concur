"""Locale regressions use synthetic data only; never log into Concur."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import os
import pytest
from playwright.sync_api import sync_playwright
from src import concur_values as v, concur_formats as f


@pytest.mark.parametrize('text', ['2026/9/17','9/17/2026','17/9/2026','09.17.2026',
    '2026년 9월 17일','Sep. 17, 2026','17-Sep-2026','September 17th, 2026',
    '２０２６／０９／１７','\u200e09/17/2026\u200f','2026-09-17T23:30:00-11:00'])
def test_full_dates(text):
    assert v.resolve_dates([{'date':text}]) == [date(2026,9,17)]


@pytest.mark.parametrize('text', ['9/8/2026','01/02/2026','9/8','26/9/8','02/30/2026',
    '2026-09-17 - 2026-09-18','2026/09-17','2026-09/17','pending',''])
def test_no_date_guessing(text):
    assert v.resolve_dates([{'date':text}]) == [None]


@pytest.mark.parametrize('order,wanted', [('MDY',date(2026,9,8)),('DMY',date(2026,8,9))])
def test_explicit_date_order_and_restore(order,wanted):
    with f.using_formats({'concur_date_order':order}):
        assert v.resolve_dates([{'date':'09/08/2026'}]) == [wanted]
    assert v.resolve_dates([{'date':'09/08/2026'}]) == [None]


def test_date_conflict_not_overridden():
    with f.using_formats({'concur_date_order':'MDY'}):
        assert v.resolve_dates([{'date':'09/08/2026','dateISO':'2026-08-09'}]) == [None]
        assert v.resolve_dates([{'date':'2026-09-08','dateISO':'2026-02-30'}]) == [None]
        assert v.resolve_dates([{'date':'17/09/2026'}]) == [None]


DOT = [('17,000.00',17000), ('KRW\xa017,000',17000),('₩１７，０００',17000),
       ('17\u202f000.00원',17000),('KRW (1,000)',-1000),('(KRW 1,000)',-1000),
       ('−1,000.00',-1000),('1000-',-1000),('0.00',0),('-0',0),
       ('9007199254740993',9007199254740993)]
COMMA = [('17.000,00',17000),('₩17.000',17000),('17\u202f000,00',17000),
         ('(1.000,00)',-1000),('1.000-',-1000),('17,00',17),('0,00',0)]
BAD = ['USD 17000','$17,000','€17000','17000.50','17O00','(-1000)','--1000',
       '-1000-','1,70,00','',None,'NaN','Infinity','1e4','17000%']


@pytest.mark.parametrize('style,text,expected', [('DOT',*x) for x in DOT]+[('COMMA',*x) for x in COMMA])
def test_amount_formats(style,text,expected):
    with f.using_formats({'concur_number_style':style}):
        assert v.parse_amount(text) == expected


@pytest.mark.parametrize('text', BAD+['17.000','17,00','17000.001'])
def test_no_rounding_no_foreign_currency(text):
    assert v.parse_amount(text) is None


@pytest.mark.parametrize('text,wanted', [('경비 KRW 17 000 날짜 2026-09-17',17000),
    ('경비 KRW (17,000) 날짜 2026-09-17',-17000), ('경비 KRW 17,000.50 날짜 2026-09-17',None),
    ('경비 KRW 17 00 날짜 2026-09-17',None), ('KRW 1,000 USD 2',None),
    ('KRW 17O00',None), ("KRW 1'000",None), ('KRW 1,000 - 날짜 2026-09-17',1000),
    ('경비 17,000원 날짜 2026-09-17',17000)])
def test_complete_summary_token(text,wanted):
    assert v.amount_from_summary(text) == wanted


def test_comma_summary():
    with f.using_formats({'concur_number_style':'COMMA'}):
        assert v.amount_from_summary('경비 KRW 17.000,00 날짜 2026-09-17') == 17000


@pytest.fixture(scope='module')
def page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        page = browser.new_page()
        yield page
        browser.close()


@pytest.mark.parametrize('style,text,wanted', [('DOT',*x) for x in DOT]+[('COMMA',*x) for x in COMMA]+
    [('DOT',text,None) for text in BAD if text is not None])
def test_python_js_amount_agree(page,style,text,wanted):
    with f.using_formats({'concur_number_style':style}):
        js = '(s) => {' + f.amount_normalizer_js() + ' return integerAmount(s); }'
        assert page.evaluate(js,text) == (str(wanted) if wanted is not None else None)


def test_no_negative_or_decimal_identity_loss(page):
    page.set_content('<input id="transactionAmount">')
    for raw, expected in [('17,000.00','17000'),('-17,000','-17000'),('(17,000)','-17000')]:
        page.fill('#transactionAmount',raw)
        assert page.evaluate(f.amount_check_js(),expected)
        assert not page.evaluate(f.amount_check_js(),'1700000')
        if expected[0] == '-':
            assert not page.evaluate(f.amount_check_js(),'17000')
    page.fill('#transactionAmount','170.00')
    assert not page.evaluate(f.amount_check_js(),'17000')


@pytest.mark.parametrize('value', ['09/17/2026 - 09/18/2026','17/09/2026 – 18/09/2026',
                                  '2026.09.17 ~ 2026.09.18'])
def test_range_compare_values_not_iso_substring(value):
    assert f.range_matches(value,date(2026,9,17),date(2026,9,18))
    assert not f.range_matches(value,date(2026,9,18),date(2026,9,17))


def test_empty_range_does_not_guess_format():
    with pytest.raises(ValueError):
        f.format_range(date(2026,9,17),date(2026,9,18))
    assert f.format_range(date(2026,9,17),date(2026,9,18),placeholder='MM/DD/YYYY - MM/DD/YYYY') == '09/17/2026 - 09/18/2026'
    with f.using_formats({'concur_date_order':'DMY'}):
        assert f.format_range(date(2026,9,17),date(2026,9,18)) == '17/09/2026 - 18/09/2026'


def test_contexts_do_not_leak_between_accounts():
    def run(order):
        with f.using_formats({'concur_date_order':order}):
            return v.resolve_dates([{'date':'09/08/2026'}])[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run,['MDY','DMY'])) == [date(2026,9,8),date(2026,8,9)]
    assert f.DATE_ORDER.get() == 'AUTO'


def test_saved_config_applies_to_runs(monkeypatch):
    from src import settings
    monkeypatch.setattr(settings,'load',lambda:{'concur_date_order':'MDY'})
    @f.configured_run
    def run(cfg=None):
        return v.resolve_dates([{'date':'09/08/2026'}])[0]
    assert run() == date(2026,9,8)
    assert run({'concur_date_order':'DMY'}) == date(2026,8,9)
    assert run({}) is None
    assert f.DATE_ORDER.get() == 'AUTO'


def test_text_normalization_does_not_erase_user_content():
    assert v.same_text('출장\r\n목적\u00a0확인','출장\n목적 확인')
    assert not v.same_text('출장  목적','출장 목적')
    assert not v.same_text('001','1')


def test_lodging_input_uses_current_format(page):
    from src import fix_expenses as fx
    page.set_content('<input id="hotelCheckinDate-date-input-field-input" value="09/17/2026 - 09/18/2026">')
    assert fx._set_date_range(page,date(2026,9,17),date(2026,9,18)) is False
    assert fx._set_date_range(page,date(2026,9,18),date(2026,9,20)) is True
    assert page.input_value(fx.DATE_RANGE_FIELD) == '09/18/2026 - 09/20/2026'


def test_lodging_unknown_format_does_not_type(page):
    from src import fix_expenses as fx
    page.set_content('<input id="hotelCheckinDate-date-input-field-input" value="unrecognized">')
    with pytest.raises(fx.AttachError,match='서식'):
        fx._set_date_range(page,date(2026,9,17),date(2026,9,18))
    assert page.input_value(fx.DATE_RANGE_FIELD) == 'unrecognized'


def test_native_number_input_keeps_dot_format(page):
    page.set_content('<input id="transactionAmount" type="number" value="17000.00">')
    with f.using_formats({'concur_number_style':'COMMA'}):
        assert page.evaluate(f.amount_check_js(),'17000')
        assert not page.evaluate(f.amount_check_js(),'1700000')


def test_field_placeholder_resolves_ambiguous_dates(page):
    from src import fix_expenses as fx
    page.set_content('<input id="hotelCheckinDate-date-input-field-input" placeholder="MM/DD/YYYY - MM/DD/YYYY" value="09/01/2026 - 09/03/2026">')
    assert fx._set_date_range(page,date(2026,9,1),date(2026,9,3)) is False
    assert fx._set_date_range(page,date(2026,9,4),date(2026,9,5)) is True
    assert page.input_value(fx.DATE_RANGE_FIELD) == '09/04/2026 - 09/05/2026'


def test_lodging_unknown_saved_amount_is_not_zero(monkeypatch):
    from src import concur_driver as cd
    from src import attach_receipts as ar, fix_expenses as fx
    page = Mock()
    def evaluate(script, *args):
        if script == fx.ITEMIZATION_STATE_JS: return {'empty':False}
        if script == fx.ROOM_RATE_INPUTS_JS: return [{'value':''}]
        return '2026-09-17 - 2026-09-18'
    page.evaluate.side_effect = evaluate
    page.get_attribute.return_value = ''
    driver = cd.Driver(page,'report',None)
    monkeypatch.setattr(driver,'guard',lambda:None)
    monkeypatch.setattr(cd.ui,'wait_condition',lambda *args:True)
    monkeypatch.setattr(fx,'_open_tab',lambda *args:None)
    row=ar.Row(0,date(2026,9,17),0,'','E1')
    plan=fx.Plan(row,None,'숙박비',lodging=fx.Lodging(date(2026,9,17),date(2026,9,18),'',''))
    assert driver.verify_edit(plan) is None


def test_format_settings_dialog_saves_without_touching_concur(monkeypatch):
    import tkinter as tk
    from tkinter import ttk
    from src import settings
    app = tk.Tk()
    app.withdraw()
    app.cfg = {}
    saved = []
    monkeypatch.setattr(settings,'save',lambda cfg:saved.append(dict(cfg)))
    try:
        f.show_settings(app)
        dialog = next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
        box = dialog.winfo_children()[0]
        combos = [w for w in box.winfo_children() if isinstance(w,ttk.Combobox)]
        combos[0].set('월 / 일 / 년')
        combos[1].set('1.234,00 (소수점: 쉼표)')
        next(w for w in box.winfo_children() if isinstance(w,ttk.Button) and w.cget('text')=='저장').invoke()
        assert saved[-1]['concur_date_order']=='MDY'
        assert app.cfg['concur_number_style']=='COMMA'
        assert not dialog.winfo_exists()
    finally:
        app.destroy()
