"""Read-only synthetic DOM tests: no Concur account, clicks or writes."""
import os
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright
from src.concur_rows import READ_ROWS_JS, rows_observed


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        # Fail rather than silently skip when this targeted test lacks a browser.
        instance = pw.chromium.launch(headless=True,
            executable_path=os.getenv('AUTO_CONCUR_TEST_BROWSER'))
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    page = browser.new_page()
    page.route('**/*', lambda route: route.abort())
    yield page
    page.close()



def snapshot(page):
    # Supply a synthetic URL lexically; no real Concur navigation or network.
    return page.evaluate("() => { const location = new URL('https://example.invalid/nui/expense/reports/TEST'); return ("
                         + READ_ROWS_JS + ")(); }")


def hooked(identity='E1', tagged=True, style='', day='2026-09-17'):
    attr = 'data-testid="data-row"' if tagged else ''
    return f'''<div role="row" {attr} id="{identity}" style="{style}">
      <span role="cell" data-nuiexp="date-cell">{day}</span>
      <span role="cell" data-nuiexp="amount-cell">17000</span>
      <span role="cell" data-nuiexp="expense-type-cell">숙박비</span>
    </div>'''


def grid(contents, extra=''):
    return f'''<div role="grid" {extra}><div role="row">
      <span role="columnheader" aria-colindex="1">날짜</span>
      <span role="columnheader" aria-colindex="2">요청됨</span>
      <span role="columnheader" aria-colindex="3">경비 유형</span>
    </div>{contents}</div>'''


def semantic(attr='data-row-key="E1"', first='2026-09-17'):
    return f'''<div role="row" {attr}>
      <span role="cell" aria-colindex="1">{first}</span>
      <span role="cell" aria-colindex="2">17000</span>
      <span role="cell" aria-colindex="3">숙박비</span></div>'''


@pytest.mark.parametrize('width', [420, 800, 1280])
@pytest.mark.parametrize('tagged', [True, False])
def test_narrow_display_contents_keeps_rendered_cells(page, width, tagged):
    page.set_viewport_size({'width': width, 'height': 600})
    page.set_content(grid(hooked(tagged=tagged, style='display:contents')))
    assert page.locator('#E1').evaluate('e => e.getBoundingClientRect().width') == 0
    rows = snapshot(page)
    assert [(r['id'], r['date'], r['amount']) for r in rows] == [('E1', '2026-09-17', '17000')]


@pytest.mark.parametrize('attr', ['hidden', 'aria-hidden="true"', 'style="display:none"',
    'style="visibility:hidden"', 'style="visibility:collapse"', 'style="content-visibility:hidden"'])
def test_hidden_ancestors_are_excluded_even_with_contents_and_child_override(page, attr):
    hidden = hooked(identity='BAD', style='display:contents').replace('<span ', '<span style="visibility:visible" ', 1)
    page.set_content(grid(f'<div {attr}>{hidden}</div>' + hooked()))
    assert [r['id'] for r in snapshot(page)] == ['E1']


def test_offscreen_rows_not_discarded(page):
    page.set_content(grid(hooked(style='position:absolute;top:4000px')))
    assert snapshot(page)[0]['id'] == 'E1'


def test_hidden_field_can_be_read_when_another_cell_is_visible(page):
    html = hooked(style='display:contents').replace('data-nuiexp="date-cell"',
        'data-nuiexp="date-cell" style="display:none"')
    page.set_content(grid(html))
    assert snapshot(page)[0]['date'] == '2026-09-17'


def test_zero_size_row_without_rendered_cells_is_not_a_record(page):
    page.set_content('<div data-testid="data-row" id="X" style="display:contents"></div>')
    assert snapshot(page) == []


def test_semantic_fallback_and_summary_exclusion(page):
    page.set_content(grid(semantic() + semantic('', '합계')))
    rows = snapshot(page)
    assert len(rows) == 1 and rows[0]['id'] == 'E1' and rows[0]['amount'] == '17000'


def test_plain_html_table_and_tfoot(page):
    page.set_content('''<table><thead><tr><th>날짜</th><th>요청됨</th><th>경비 유형</th></tr></thead>
      <tbody><tr data-row-key="E1"><td>2026-09-17</td><td>17000</td><td>숙박비</td></tr></tbody>
      <tfoot><tr data-testid="data-row" id="TOTAL"><td>합계</td><td>17000</td><td></td></tr></tfoot></table>''')
    rows = snapshot(page)
    assert len(rows) == 1 and rows[0]['id'] == 'E1' and rows[0]['date'] == '2026-09-17'


def test_unrelated_table_does_not_become_an_expense(page):
    page.set_content(grid(semantic()).replace('경비 유형', '주문 유형'))
    assert snapshot(page) == []


def test_arbitrary_dom_id_is_not_used_as_expense_id(page):
    page.set_content(grid(semantic('id="ui-widget-12"')))
    rows = snapshot(page)
    assert len(rows) == 1 and rows[0]['id'] is None


@pytest.mark.parametrize('href,expected', [('/nui/expense/reports/TEST/expenses/E1', 'E1'),
    ('/nui/expense/reports/OTHER/expenses/E1', None),
    ('https://other.concursolutions.com/nui/expense/reports/TEST/expenses/E1', None),
    ('http://[', None)])
def test_link_identity_is_scoped_to_same_report(page, href, expected):
    page.set_content(grid(semantic('id="ui-widget-12"', f'<a href="{href}">2026-09-17</a>')))
    assert snapshot(page)[0]['id'] == expected


def test_missing_values_remain_for_target_scoped_review(page):
    page.set_content(grid(hooked(tagged=False, day='')))
    rows = snapshot(page)
    assert len(rows) == 1 and rows[0]['date'] == ''


@pytest.mark.parametrize('attrs', ['aria-busy="true"', 'aria-rowcount="4"'])
def test_partial_or_busy_list_still_blocks(page, attrs):
    page.set_content(grid(hooked(tagged=False, style='display:contents'), attrs))
    rows = snapshot(page)
    assert rows[0]['readProblem']
    assert not rows_observed([SimpleNamespace(read_problem=r['readProblem']) for r in rows])


def test_mixed_tagged_and_semantic_rows_are_both_counted(page):
    page.set_content(grid(hooked() + semantic('data-row-key="E2"'), 'aria-rowcount="3"'))
    rows = snapshot(page)
    assert [r['id'] for r in rows] == ['E1', 'E2']
    assert not any(r['readProblem'] for r in rows)


def test_nested_role_row_does_not_duplicate_tagged_wrapper(page):
    inner = hooked(identity='E2', tagged=False)
    page.set_content(grid(f'<div data-testid="data-row" id="E1">{inner}</div>'))
    rows = snapshot(page)
    assert len(rows) == 1 and rows[0]['id'] == 'E1'
