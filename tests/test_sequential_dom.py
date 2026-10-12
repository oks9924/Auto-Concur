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


def _상세(**kw):
    base = dict(dateField=True, amountField=True, vendorField=True,
                amount="KRW 17,300", currency="KRW", vendor="가게", placeholder="")
    return {**base, **kw}


def _목록행(when=None):
    from src import attach_receipts as ar

    return ar.Row(0, when, None, "", "ID1", "", "")


@pytest.fixture
def 기억없이(monkeypatch):
    """배운 순서를 설정에 쓰지 않는다. 테스트가 사람 설정을 건드리면 안 된다."""
    from src import concur_detail as cd

    monkeypatch.setattr(cd, "_save_order", lambda order: None)
    cd._LEARNED.set(None)
    return cd


def test_목록이_읽은_날짜로_모호함을_가른다(기억없이):
    """'10/09/2026' 은 10월 9일일 수도 9월 10일일 수도 있다.

    목록은 전체 행을 모아 읽어서 서식을 스스로 추론하는데, 상세는 한 건씩
    읽어서 그 추론이 안 걸렸다. 같은 경비를 두 번 읽어 같은 날을 가리키면
    그건 추측이 아니라 근거다.
    """
    from datetime import date

    got = 기억없이.decode_detail(_상세(date="10/09/2026"), _목록행(date(2026, 9, 10)))
    assert got.when == date(2026, 9, 10)
    assert 기억없이.learned_order() == "DMY"


def test_한_번_확인한_순서를_그_뒤에_쓴다(기억없이):
    """한 계정이 날짜를 두 가지로 보여주지는 않는다."""
    from datetime import date

    기억없이.decode_detail(_상세(date="25/09/2026"), _목록행())  # 25일 - 월이 될 수 없다
    assert 기억없이.learned_order() == "DMY"

    got = 기억없이.decode_detail(_상세(date="03/09/2026"), _목록행())
    assert got.when == date(2026, 9, 3)


def test_근거가_없으면_그대로_멈춘다(기억없이):
    """알아서 맞추되, 지어내지는 않는다. 엉뚱한 경비에 붙는 것보다 멈추는 게 낫다."""
    from src import attach_receipts as ar

    with pytest.raises(ar.AttachError) as err:
        기억없이.decode_detail(_상세(date="03/09/2026"), _목록행())
    assert "일/월/년이면 2026-09-03" in str(err.value)


def test_목록과_상세가_어긋나면_믿지_않는다(기억없이):
    """두 읽기가 다른 날을 가리키면 그중 하나는 틀린 것이다."""
    from datetime import date

    from src import attach_receipts as ar

    with pytest.raises(ar.AttachError):
        기억없이.decode_detail(_상세(date="03/09/2026"), _목록행(date(2026, 12, 25)))


def test_서로_다른_순서가_보이면_아무것도_안_믿는다(기억없이):
    """있을 수 없는 일이다. 일어났다면 우리 판단이 틀린 것이므로 사람에게 넘긴다."""
    from datetime import date

    기억없이.decode_detail(_상세(date="25/09/2026"), _목록행())      # DMY
    기억없이.decode_detail(_상세(date="09/25/2026"), _목록행())      # MDY
    assert 기억없이.learned_order() is None


def test_원본_거래와_맞는_해석이_하나뿐이면_그걸로_본다(기억없이):
    """실측 2026-10-12: '07/09/2026' - 7월 9일인가 9월 7일인가.

    리포트는 한 달치만 올린다. 9월 전표만 있는 리포트에 7월은 있을 수 없다.
    """
    from datetime import date

    with 기억없이.using_known_dates([date(2026, 9, 3), date(2026, 9, 7)]):
        got = 기억없이.decode_detail(_상세(date="07/09/2026"), _목록행())
    assert got.when == date(2026, 9, 7)
    assert 기억없이.learned_order() == "DMY"


def test_둘_다_맞으면_고르지_않는다(기억없이):
    """날짜로 가릴 수 없다는 뜻이다. 여기서 아무거나 고르면 엉뚱한 데 붙는다."""
    from datetime import date

    from src import attach_receipts as ar

    with 기억없이.using_known_dates([date(2026, 7, 9), date(2026, 9, 7)]):
        with pytest.raises(ar.AttachError):
            기억없이.decode_detail(_상세(date="07/09/2026"), _목록행())


def test_아무것도_안_맞으면_고르지_않는다(기억없이):
    from datetime import date

    from src import attach_receipts as ar

    with 기억없이.using_known_dates([date(2026, 12, 25)]):
        with pytest.raises(ar.AttachError):
            기억없이.decode_detail(_상세(date="07/09/2026"), _목록행())


def test_하루_차이는_같은_거래로_본다(기억없이):
    """카드 매입 처리 때문에 Concur 날짜가 거래일과 하루 어긋난다."""
    from datetime import date

    with 기억없이.using_known_dates([date(2026, 9, 8)]):
        got = 기억없이.decode_detail(_상세(date="07/09/2026"), _목록행())
    assert got.when == date(2026, 9, 7)
