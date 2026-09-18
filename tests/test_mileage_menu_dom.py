import os
import pytest
from playwright.sync_api import sync_playwright
from src.mileage_concur import EXACT_TEXT_JS, MILEAGE_TYPE_JS, COMBO_JS, FIELD_JS, MILEAGE_FORM_READY_JS, OPTION_JS, RECEIPT_VIEW_JS


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True, executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        yield b
        b.close()


def test_manual_create_nested_div_span_is_clickable(browser):
    page=browser.new_page()
    page.set_content("""
    <div id='menu'>
      <div id='row' onclick="document.body.dataset.clicked='manual'">
        <span>✎</span><div><span>수동으로 경비 생성</span></div>
      </div>
    </div>
    """)
    selector=page.evaluate(EXACT_TEXT_JS,['수동으로 경비 생성'])
    assert selector
    page.locator(selector).click()
    assert page.get_attribute('body','data-clicked')=='manual'
    page.close()


def test_mileage_type_nested_div_span_is_clickable(browser):
    page=browser.new_page()
    page.set_content("""
    <div id='types'>
      <div id='mileage' onclick="document.body.dataset.clicked='mileage'">
        <span>🚗</span><div><span>자동차 마일리지</span></div>
      </div>
    </div>
    """)
    selector=page.evaluate(MILEAGE_TYPE_JS)
    assert selector
    page.locator(selector).click()
    assert page.get_attribute('body','data-clicked')=='mileage'
    page.close()


def test_hidden_duplicate_does_not_make_visible_label_ambiguous(browser):
    page=browser.new_page()
    page.set_content("""
    <div style='display:none'><span>수동으로 경비 생성</span></div>
    <div onclick="document.body.dataset.clicked='ok'"><span>수동으로 경비 생성</span></div>
    """)
    selector=page.evaluate(EXACT_TEXT_JS,['수동으로 경비 생성'])
    assert selector
    page.locator(selector).click()
    assert page.get_attribute('body','data-clicked')=='ok'
    page.close()


def test_active_modal_scopes_combo_away_from_background_grid(browser):
    page=browser.new_page()
    page.set_content("""
    <button data-testid='column-sort' aria-label='Vehicle'>Vehicle column</button>
    <div class='sapcnqr-dialog__body add-modal__body modal-body--no-padding'>
      <label for='vehicle'>차량 ID</label>
      <button id='vehicle' role='combobox' aria-label='차량 ID'>내 차량</button>
      <label for='distance'>거리</label><input id='distance' value=''>
      <label for='origin'>출발지</label><input id='origin' value=''>
    </div>
    """)
    selector=page.evaluate(COMBO_JS,['차량 ID','Vehicle ID','Vehicle'])
    assert selector
    assert page.locator(selector).get_attribute('id')=='vehicle'
    assert page.evaluate(MILEAGE_FORM_READY_JS)
    page.close()


def test_active_modal_scopes_fields_away_from_background(browser):
    page=browser.new_page()
    page.set_content("""
    <input id='background-origin' aria-label='Origin'>
    <div class='sapcnqr-dialog__body add-modal__body modal-body--no-padding'>
      <label for='origin'>출발지</label><input id='origin'>
      <label for='destination'>도착지</label><input id='destination'>
      <label for='distance'>거리</label><input id='distance'>
    </div>
    """)
    selector=page.evaluate(FIELD_JS,['출발지','Origin','From'])
    assert selector and page.locator(selector).get_attribute('id')=='origin'
    page.close()


def test_mileage_type_prefers_button_inside_active_modal(browser):
    page=browser.new_page()
    page.set_content("""
    <div><span>자동차 마일리지</span></div>
    <div class='sapcnqr-dialog__body add-modal__body modal-body--no-padding'>
      <button id='mileage' onclick="document.body.dataset.clicked='modal'">
        <span class='sapcnqr-button__inner expense-type-list__expense-type-button'>자동차 마일리지</span>
      </button>
    </div>
    """)
    selector=page.evaluate(MILEAGE_TYPE_JS)
    assert selector
    page.locator(selector).click()
    assert page.get_attribute('body','data-clicked')=='modal'
    page.close()


def test_field_found_when_label_is_div_span_not_html_label(browser):
    page=browser.new_page()
    page.set_content("""
    <div class='sapcnqr-dialog__body'>
      <div class='field-row'><div><span>거래 날짜</span></div><div><input id='txdate'></div></div>
      <div class='field-row'><div><span>출발지</span></div><div><input id='origin'></div></div>
      <div class='field-row'><div><span>도착지</span></div><div><input id='dest'></div></div>
    </div>
    """)
    selector=page.evaluate(FIELD_JS,['거래 날짜','Transaction Date','Date'])
    assert selector and page.locator(selector).get_attribute('id')=='txdate'
    page.close()


def test_vehicle_combo_found_from_nearby_div_label(browser):
    page=browser.new_page()
    page.set_content("""
    <div class='sapcnqr-dialog__body'>
      <div class='field-row'><div><span>차량 ID</span></div><div><button id='vehicle' role='combobox'>05두2956</button></div></div>
      <button data-testid='column-sort' aria-label='Vehicle'>background vehicle sort</button>
    </div>
    """)
    selector=page.evaluate(COMBO_JS,['차량 ID','Vehicle ID','Vehicle'])
    assert selector and page.locator(selector).get_attribute('id')=='vehicle'
    page.close()


def test_vehicle_option_accepts_kind_suffix(browser):
    page=browser.new_page()
    page.set_content("""
    <div role='listbox'>
      <div role='option' id='car'>05두2956 long</div>
    </div>
    """)
    selector=page.evaluate(OPTION_JS,'05두2956')
    assert selector and page.locator(selector).get_attribute('id')=='car'
    page.close()


def test_receipt_view_is_scoped_to_active_mileage_panel(browser):
    page=browser.new_page()
    page.set_content("""
    <button id='background' data-nuiexp='rcpt-btn-attach-receipt'
      onclick="document.body.dataset.background='yes'">영수증 첨부</button>
    <div id='sapcnqr-layout-side-panel-elements'>
      <div><span>거래 날짜</span><input id='date'></div>
      <div><span>출발지</span><input id='origin'></div>
      <button id='view' onclick="document.body.dataset.view='yes'">영수증 보기</button>
    </div>
    """)
    selector=page.evaluate(RECEIPT_VIEW_JS,['영수증 보기','View Receipt'])
    assert selector and page.locator(selector).get_attribute('id')=='view'
    page.locator(selector).click()
    assert page.get_attribute('body','data-view')=='yes'
    assert page.get_attribute('body','data-background') is None
    page.close()
