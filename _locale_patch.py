"""Temporary integration step against the previously read source, with exact-match checks."""
from pathlib import Path
ROOT = Path.cwd()


def replace(path, before, after, count=1):
    target = ROOT / path
    value = target.read_text(encoding='utf-8')
    if value.count(before) != count:
        raise RuntimeError(f'Unexpected source {path}: {value.count(before)} != {count}: {before[:65]}')
    target.write_text(value.replace(before, after), encoding='utf-8')


def main():
    for path in ('src/attach_receipts.py', 'src/fix_expenses.py', 'src/update_concur.py'):
        replace(path, 'from __future__ import annotations',
            'from __future__ import annotations\nfrom .concur_formats import configured_run, amount_check_js, room_check_js, range_matches, format_range')
        replace(path, '\ndef run(', '\n@configured_run\ndef run(')
    replace('src/attach_receipts.py', 'page.wait_for_function(WAIT_AMOUNT_JS, arg=str(slip.amount), timeout=20000)',
        'page.wait_for_function(amount_check_js(), arg=str(slip.amount), timeout=20000)')
    p = ROOT / 'src/attach_receipts.py'
    source = p.read_text(encoding='utf-8')
    a = source.index('WAIT_AMOUNT_JS = """')
    b = source.index('\n"""', a) + 4
    source = source[:a] + 'WAIT_AMOUNT_JS = amount_check_js()' + source[b:]
    p.write_text(source, encoding='utf-8')
    replace('src/fix_expenses.py', '_wait_js(page, WAIT_AMOUNT_JS,', '_wait_js(page, amount_check_js(),', count=2)
    replace('src/fix_expenses.py', '    want = f"{checkin:%Y-%m-%d} - {checkout:%Y-%m-%d}"\n', '')
    replace('src/fix_expenses.py', '    if checkin.strftime("%Y-%m-%d") in already and checkout.strftime("%Y-%m-%d") in already:\n        return False',
        '''    placeholder = page.get_attribute(DATE_RANGE_FIELD, 'placeholder') or ''
    if range_matches(already, checkin, checkout, placeholder):
        return False
    try:
        want = format_range(checkin, checkout, already, placeholder)
    except ValueError as exc:
        raise AttachError(str(exc)) from exc''')
    replace('src/fix_expenses.py', '    if checkin.strftime("%Y-%m-%d") not in shown or checkout.strftime("%Y-%m-%d") not in shown:',
        '    if not range_matches(shown, checkin, checkout, placeholder):')
    replace('src/fix_expenses.py', '''    _wait_js(page, "(want) => { const cells = (" + ROOM_RATE_INPUTS_JS +
             ")(); return cells.length === want.length && cells.every((c,i) => "
             "c.value !== '' && Number(c.value.replace(/,/g, '')) === want[i]); }",
             '객실 요금 입력 반영', arg=amounts)''',
        '''    _wait_js(page, room_check_js(ROOM_RATE_INPUTS_JS),
             '객실 요금 입력 반영', arg=[str(value) for value in amounts])''')
    replace('src/concur_driver.py', 'from .report_session import check_context',
        'from .report_session import check_context\nfrom .concur_formats import amount_check_js, range_matches\nfrom .concur_values import parse_amount, same_text')
    replace('src/concur_driver.py', 'page, ar.WAIT_AMOUNT_JS,', 'page, amount_check_js(),', count=2)
    replace('src/concur_driver.py', 'actual.strip() == expected.strip()', 'same_text(actual, expected)', count=2)
    replace('src/concur_driver.py', 'all(d.isoformat() in value for d in (lodging.checkin, lodging.checkout))',
        "range_matches(value, lodging.checkin, lodging.checkout, page.get_attribute(fx.DATE_RANGE_FIELD, 'placeholder') or '')")
    replace('src/concur_driver.py', r'''                try:
                    values = [Decimal(re.sub(r'[^0-9.\-]', '', r['value']) or '0') for r in rates]
                    checks.append(values == fx.nightly_split(plan.row.amount, lodging.nights))
                except InvalidOperation:
                    checks.append(None)''',
        '''                values = [parse_amount(r.get('value')) for r in rates]
                checks.append(None if any(value is None for value in values) else
                              values == fx.nightly_split(plan.row.amount, lodging.nights))''')
    replace('src/gui.py', 'from . import console, paths, retry, settings',
        'from . import console, paths, retry, settings\nfrom .concur_formats import show_settings as show_concur_formats')
    replace('src/gui.py', '        self.buttons = []',
        '        format_button = ttk.Button(opts, text="Concur 표시 서식", command=lambda: show_concur_formats(self))\n        format_button.pack(side="left", padx=12)\n        self.buttons = [format_button]')
    replace('src/fix_expenses.py', "'YYYY-MM-DD - YYYY-MM-DD' 라 그대로 친다. 달력을 눌러 고르는 것보다 확실하다.",
        '입력값/placeholder/명시한 날짜 순서로 확인한다. 언어만으로 날짜 순서를 추측하지 않는다.')


if __name__ == '__main__':
    main()
