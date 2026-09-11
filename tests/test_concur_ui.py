"""실제 Chromium의 합성 화면으로 재렌더링·패널·지연 전환을 검증한다.

AUTO_CONCUR_TEST_BROWSER로 이미 설치된 Chromium 실행 파일을 지정할 수 있다.
서비스 로그인/네트워크 요청 없이 set_content로만 화면을 만든다.
"""
import os
from datetime import date
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright, Error, TimeoutError

from src import concur_ui as ui, fix_expenses as fx, attach_receipts as ar


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(headless=True, executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        except Error as exc:
            pytest.skip(f'Chromium 테스트 실행 파일 없음: {exc}')
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def test_late_button_is_waited_for(page):
    page.set_content('''<script>window.hits=0; setTimeout(() => {
      document.body.innerHTML='<button id="save" onclick="window.hits++">저장</button>';
    }, 300);</script>''')
    ui.click_target(page, "() => document.querySelector('#save') ? '#save' : null", '저장', timeout=3000)
    assert page.evaluate('window.hits') == 1


def test_rerendered_target_is_rediscovered_before_click(page):
    page.set_content('''<button id="save" onclick="window.hits++">저장</button>
      <div id="cover" style="position:fixed;inset:0;background:white;z-index:3"></div>
      <script>window.hits=0; setTimeout(() => {
        document.querySelector('#save').outerHTML='<button id="save" onclick="window.hits++">저장</button>';
        document.querySelector('#cover').remove();
      }, 900);</script>''')
    script = """() => { const b = document.querySelector('#save');
      if (!b) return null; b.setAttribute('data-auto-target', '1'); return '[data-auto-target="1"]'; }"""
    ui.click_target(page, script, '저장', timeout=4000)
    assert page.evaluate('window.hits') == 1


def test_save_stays_in_active_dialog(page):
    page.set_content('''<button onclick="window.wrong++">저장</button>
      <div role="dialog"><button onclick="window.right++">저장</button></div>
      <script>window.wrong=0;window.right=0;</script>''')
    ui.click_target(page, fx.SAVE_BUTTONS_JS, '명세 저장', fx.SAVE_ITEMIZATION, timeout=3000)
    assert page.evaluate('[window.wrong, window.right]') == [0, 1]


def test_english_expense_save_ignores_hidden_copy(page):
    page.set_content('''<button style="display:none">경비 저장</button>
      <button data-nuiexp="exp-save-expense" onclick="window.saved=true">Save Expense</button>
      <script>window.saved=false;</script>''')
    ui.click_target(page, ui.SAVE_BUTTONS_JS, '저장', '경비 저장,Save Expense', timeout=2000)
    assert page.evaluate('window.saved')


def test_itemization_update_confirmation_uses_update_not_cancel(page):
    page.set_content('''<div role="dialog">다른 항목을 업데이트하시겠습니까?
      동일한 변경으로 이 경비의 항목별 명세 및 할당도 업데이트하시겠습니까?
      <button onclick="window.action='update';this.parentElement.remove()">업데이트</button>
      <button onclick="window.action='no'">업데이트하지 마십시오.</button>
      <button onclick="window.action='cancel'">취소</button></div>''')
    assert '업데이트했습니다' in fx._dismiss_dialog(page, wait_ms=100)
    assert page.evaluate('window.action') == 'update'


def test_update_confirmation_then_following_rejection(page):
    page.set_content('''<div role="dialog">다른 항목을 업데이트하시겠습니까? 항목별 명세
      <button onclick="this.parentElement.remove();document.querySelector('#next').style.display='block'">업데이트</button></div>
      <div role="alertdialog" id="next" style="display:none">오류 유효한 정보를 제공해야 합니다.
      <button onclick="this.parentElement.remove()">닫기</button></div>''')
    assert fx.REJECTED_RE.search(fx._dismiss_dialog(page, wait_ms=200))


def test_type_code_selection_accepts_english_display_label(page):
    page.set_content('''<div role="combobox" aria-label="Expense Type"
      onclick="document.querySelector('li').style.display='block'">Other</div>
      <li role="option" id="type-_-_-LODNG-_-_-1" style="display:none"
      onclick="document.querySelector('[role=combobox]').textContent='Hotel';this.style.display='none'">Hotel</li>''')
    fx._set_type(page, 'LODNG', '숙박비')
    assert page.locator('[role=combobox]').inner_text() == 'Hotel'


def test_hidden_combo_and_options_do_not_win(page):
    page.set_content('''<div role="combobox" aria-label="숙박위치" style="display:none">잘못된 값</div>
      <div role="combobox" aria-label="숙박위치">국내</div>
      <li role="option" style="display:none">Others</li><li role="option" id="good">Others</li>''')
    assert page.evaluate(fx.COMBO_VALUE_JS, '숙박위치') == '국내'
    selector = page.evaluate(fx.SELECT_OPTION_JS, 'Others')
    assert page.locator(selector).get_attribute('id') == 'good'


def test_combo_waits_for_delayed_value(page):
    page.set_content('''<div id="custom16" role="combobox" onclick="document.querySelector('li').style.display='block'">Old</div>
      <li role="option" style="display:none" onclick="setTimeout(() => document.querySelector('#custom16').textContent='Others', 900)">Others</li>''')
    assert fx._pick_from_combo(page, 'custom16', 'Others', 'Booking channel')
    assert page.locator('#custom16').inner_text() == 'Others'


def test_hidden_room_rate_template_does_not_indicate_form(page):
    page.set_content('''<input id="SameRoomRateItemization.roomRate.0" style="display:none">
      <div class="side-panel__side">항목별 명세 없음<button>항목별 명세 추가</button></div>''')
    state = page.evaluate(fx.ITEMIZATION_STATE_JS)
    assert state['empty'] and not state['form']
    assert page.evaluate(fx.ROOM_RATE_INPUTS_JS) == []


def test_room_rate_rows_can_arrive_in_separate_renders(page):
    page.set_content('''<div class="side-panel__side" id="panel">
      <input id="DifferentRoomRateItemization.roomRate.0"></div>
      <script>setTimeout(() => document.querySelector('#panel').insertAdjacentHTML('beforeend',
      '<input id="DifferentRoomRateItemization.roomRate.1">'), 900)</script>''')
    fx._fill_room_rates(page, [1000, 2000])
    assert [cell['value'] for cell in page.evaluate(fx.ROOM_RATE_INPUTS_JS)] == ['1000', '2000']


def test_actual_click_is_not_retried_after_uncertain_timeout(monkeypatch):
    page = Mock()
    page.evaluate.return_value = '#save'
    target = page.locator.return_value
    target.click.side_effect = [None, TimeoutError('uncertain after dispatch')]
    monkeypatch.setattr(ui, 'diagnose', lambda *a: '')
    with pytest.raises(ui.ConcurUIError, match='자동 재클릭하지'):
        ui.click_target(page, '() => "#save"', '저장')
    assert target.click.call_count == 2  # 준비 확인 1회, 실제 클릭 1회
    assert target.click.call_args_list[0].kwargs['trial'] is True


def test_wait_survives_navigation_context_replacement():
    page = Mock()
    page.evaluate.side_effect = [Error('Execution context was destroyed'), False, True]
    assert ui.wait_condition(page, '() => true', '상세 준비', timeout=1000)
    assert page.evaluate.call_count == 3


def test_incomplete_rows_are_not_used_for_matching(monkeypatch):
    page = Mock()
    incomplete = [ar.Row(0, date(2026, 8, 1), 1000, '', 'E1'), ar.Row(1, None, None, '', 'E2')]
    complete = [incomplete[0], ar.Row(1, date(2026, 8, 2), 2000, '', 'E2')]
    read = Mock(side_effect=[incomplete, complete, complete])
    monkeypatch.setattr(ar, 'read_rows', read)
    assert ar.rows_when_ready(page, tries=3, wait_ms=1) == complete
    assert read.call_count == 3


def test_incomplete_rows_fail_instead_of_silently_skipping(monkeypatch):
    monkeypatch.setattr(ar, 'read_rows', lambda p: [ar.Row(0, None, None, '', 'E1')])
    monkeypatch.setattr(ui, 'diagnose', lambda *a: '')
    with pytest.raises(ar.AttachError, match='일부 행만으로'):
        ar.rows_when_ready(Mock(), tries=2, wait_ms=1)
