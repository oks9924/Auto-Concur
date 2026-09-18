import os
import pytest
from playwright.sync_api import sync_playwright
from src.mileage_concur import EXACT_TEXT_JS, MILEAGE_TYPE_JS


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
