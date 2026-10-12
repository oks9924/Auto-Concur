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


def test_날짜를_못_읽은_이유를_둘로_가른다():
    """실측 2026-10-12: 14건 전부 '상세 거래 날짜 미확인'.

    한 메시지가 두 원인을 덮고 있었다 - 입력칸을 못 고른 것과, 칸은 찾았는데
    글자를 날짜로 못 읽은 것. 어느 쪽인지 몰라 좁힐 수가 없었다.
    """
    import pytest

    from src import attach_receipts as ar
    from src.concur_detail import decode_detail

    seed = ar.Row(0, None, None, "", "ID1", "", "")

    # 칸을 못 골랐다 (one() 은 보이는 것이 정확히 하나일 때만 준다)
    with pytest.raises(ar.AttachError) as err:
        decode_detail({"dateField": False, "amountField": True, "vendorField": True,
                       "dateHere": [{"id": "transactionDate-a", "value": "2026-09-03", "shown": True},
                                    {"id": "transactionDate-b", "value": "", "shown": True}],
                       "datePage": []}, seed)
    말 = str(err.value)
    assert "입력칸을 고르지 못했습니다" in 말
    assert "후보 2개" in 말 and "transactionDate-a" in 말

    # 칸은 찾았는데 글자를 못 읽었다
    with pytest.raises(ar.AttachError) as err:
        decode_detail({"dateField": True, "date": "03 Sept 2026", "placeholder": "",
                       "amountField": True, "vendorField": True}, seed)
    말 = str(err.value)
    assert "'03 Sept 2026'" in 말 and "날짜로 읽지 못했습니다" in 말


def test_입력칸이_아예_없으면_그렇게_말한다():
    import pytest

    from src import attach_receipts as ar
    from src.concur_detail import decode_detail

    with pytest.raises(ar.AttachError) as err:
        decode_detail({"dateField": False, "amountField": True, "vendorField": True,
                       "dateHere": [], "datePage": []},
                      ar.Row(0, None, None, "", "ID1", "", ""))
    assert "거래일 입력칸이 없습니다" in str(err.value)


def test_모호한_날짜는_둘_다_읽어서_보여준다():
    """실측 2026-10-12: '10/09/2026'.

    10월 9일인지 9월 10일인지는 화면만 봐서는 알 수 없다. 둘 다 적어주면
    전표 날짜와 맞는 쪽을 사람이 바로 고를 수 있다 - 설정 한 번이면 끝난다.
    """
    import pytest

    from src import attach_receipts as ar
    from src.concur_detail import decode_detail

    with pytest.raises(ar.AttachError) as err:
        decode_detail({"dateField": True, "date": "10/09/2026", "placeholder": "",
                       "amountField": True, "vendorField": True},
                      ar.Row(0, None, None, "", "ID1", "", ""))
    말 = str(err.value)
    assert "일/월/년이면 2026-09-10" in 말
    assert "월/일/년이면 2026-10-09" in 말


def test_모호하지_않으면_군말을_안_붙인다():
    from src.concur_detail import _reading_hint

    assert _reading_hint("2026-09-10") == ""  # 년/월/일은 해석이 하나뿐
    assert _reading_hint("25/09/2026") == ""  # 25는 월이 될 수 없다
