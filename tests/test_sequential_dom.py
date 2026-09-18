"""Synthetic DOM regressions. Never contact Concur or use uploaded transaction data."""
import os
from datetime import date
from dataclasses import replace
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright, Error
from src import attach_receipts as ar
from src.concur_detail import DETAIL_JS, read_detail, decode_detail, DetailDriver

URL = 'https://eu2.concursolutions.com/nui/expense/reports/TEST'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch(headless=True, executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        except Error as exc:
            if os.getenv('CI'):
                raise
            pytest.skip(str(exc))
        yield b
        b.close()


@pytest.fixture
def page(browser):
    p=browser.new_page()
    yield p
    p.close()


def detail(day='2026-09-18', amount='12345', vendor='Synthetic store', currency='KRW'):
    return f'''<label for="transactionDate-input">거래 날짜</label>
    <input id="transactionDate-input" value="{day}" placeholder="YYYY-MM-DD">
    <input id="transactionAmount" name="transactionAmount" value="{amount}">
    <input id="vendorName" name="vendorName" value="{vendor}">
    <input name="transactionCurrencyName" value="{currency}">
    <label for="expenseType">경비 유형</label><div role="combobox" id="expenseType">주차비</div>'''


def seed():
    return ar.Row(0,None,None,'','E1')


def test_detail_reads_values_not_labels(page):
    page.set_content(detail())
    r=decode_detail(page.evaluate(DETAIL_JS),seed())
    assert r.when==date(2026,9,18) and r.amount==12345 and r.vendor=='Synthetic store'


def test_hidden_template_does_not_shadow_visible_form(page):
    page.set_content('<div style="display:none">'+detail(amount='999')+'</div>'+detail())
    assert decode_detail(page.evaluate(DETAIL_JS),seed()).amount==12345


def test_visible_duplicate_fields_are_not_arbitrarily_chosen(page):
    page.set_content(detail()+detail(amount='999'))
    with pytest.raises(ar.AttachError,match='금액'):
        decode_detail(page.evaluate(DETAIL_JS),seed())


def test_read_detail_navigates_same_id_and_waits_delayed_form(page):
    html=detail().replace('value="12345"','value=""')+'''<script>setTimeout(()=>{
    document.querySelector('#transactionAmount').value='12345';},220)</script>'''
    page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
    page.goto(URL)
    r=read_detail(page,URL,seed(),timeout_ms=2500)
    assert page.url==URL+'/expenses/E1' and r.amount==12345


def test_explicit_other_currency_blocks_same_numeric_value(page):
    page.set_content(detail(currency='USD'))
    with pytest.raises(ar.AttachError,match='통화'):
        decode_detail(page.evaluate(DETAIL_JS),seed())


def expense(i, empty=False):
    return f'''<div role="row" data-testid="data-row" id="E{i}" style="display:contents">
    <div role="cell" data-nuiexp="date-cell">{'' if empty else '2026-09-18'}</div>
    <div role="cell" data-nuiexp="amount-cell">{'' if empty else 'KRW 12345'}</div>
    <div role="cell" data-nuiexp="expense-type-cell">주차비</div>
    <div role="cell" data-nuiexp="receipts-cell"><button data-nuiexp="receipt-thumbnail-button-existing.jpeg"></button></div></div>'''


def table(body):
    return '<div role="table" data-testid="report-entries-table"><div role="row"><div role="columnheader">날짜</div><div role="columnheader">요청됨</div><div role="columnheader">경비 유형</div><div role="columnheader">영수증</div></div>'+body+'</div>'


def test_footer_columns_not_an_expense_and_real_last_row_kept(page):
    body=''.join(expense(i) for i in range(16))
    footer='<div role="row" data-testid="footer-columns" style="display:contents"><div role="cell"></div><div role="cell">KRW 197520</div></div>'
    page.set_content(table(body+footer))
    rows=ar.read_rows(page)
    assert len(rows)==16 and rows[-1].expense_id=='E15'
    assert all(r.when and r.amount==12345 for r in rows)


def test_unknown_real_expense_not_discarded_as_footer(page):
    page.set_content(table(expense(1)+expense(2,empty=True)))
    rows=ar.read_rows(page)
    assert len(rows)==2 and rows[1].expense_id=='E2' and rows[1].amount is None


def test_target_receipt_does_not_require_unrelated_rows_to_parse(page,tmp_path):
    html=table(expense(1)+expense(2,empty=True))
    page.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
    page.goto(URL)
    driver=DetailDriver(page,URL,tmp_path)
    found=driver.receipt_row(seed())
    assert found.has_receipt is True and found.receipt_file=='existing.jpeg'
