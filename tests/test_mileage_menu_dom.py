import os
import pytest
from playwright.sync_api import sync_playwright
from src.mileage_concur import EXACT_TEXT_JS, MILEAGE_TYPE_JS, COMBO_JS, FIELD_JS, MILEAGE_FORM_READY_JS


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


def test_field_found_from_div_caption_and_sibling_input(browser):
    page=browser.new_page()
    page.set_content("""
    <div class='sapcnqr-dialog__body add-modal__body modal-body--no-padding'>
      <div class='field'>
        <div class='caption'><span>거래 날짜</span></div>
        <div class='control'><input id='transaction-date' value=''></div>
      </div>
      <div class='field'><div><span>출발지</span></div><div><input id='origin'></div></div>
      <div class='field'><div><span>도착지</span></div><div><input id='destination'></div></div>
    </div>
    """)
    selector=page.evaluate(FIELD_JS,['거래 날짜','Transaction Date','Date'])
    assert selector and page.locator(selector).get_attribute('id')=='transaction-date'
    page.close()


def test_vehicle_combo_found_from_div_caption_and_sibling_button(browser):
    page=browser.new_page()
    page.set_content("""
    <button data-testid='column-sort' aria-label='Vehicle'>background vehicle</button>
    <div class='sapcnqr-dialog__body add-modal__body modal-body--no-padding'>
      <div class='field'>
        <div><span>차량 ID</span></div>
        <div><button id='vehicle-combo' role='combobox'>05두2956</button></div>
      </div>
      <div><span>거리</span><input id='distance'></div>
    </div>
    """)
    selector=page.evaluate(COMBO_JS,['차량 ID','Vehicle ID','Vehicle'])
    assert selector and page.locator(selector).get_attribute('id')=='vehicle-combo'
    page.close()


def test_actual_mileage_labels_can_resolve_all_text_fields(browser):
    page=browser.new_page()
    page.set_content("""
    <div class='sapcnqr-dialog__body'>
      <div><span>거래 날짜</span><div><input id='date'></div></div>
      <div><span>출발지</span><div><input id='from'></div></div>
      <div><span>도착지</span><div><input id='to'></div></div>
      <div><span>거리</span><div><input id='km'></div></div>
      <div><span>탑승자 수</span><div><input id='passengers'></div></div>
      <div><span>설명</span><div><textarea id='description'></textarea></div></div>
    </div>
    """)
    cases=[
      (['거래 날짜','Transaction Date','Date'],'date'),
      (['출발지','출발 위치','Origin','From'],'from'),
      (['도착지','도착 위치','Destination','To'],'to'),
      (['거리','Distance'],'km'),
      (['탑승자 수','Passengers','Passenger Count'],'passengers'),
      (['설명','Description','Business Purpose'],'description'),
    ]
    for names,wanted in cases:
        selector=page.evaluate(FIELD_JS,names)
        assert selector and page.locator(selector).get_attribute('id')==wanted
    page.close()
