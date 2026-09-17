"""Explicit per-run Concur display formats; never infer them from UI language."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
import json
import re

DATE_ORDER = ContextVar('concur_date_order', default='AUTO')
NUMBER_STYLE = ContextVar('concur_number_style', default='DOT')


@contextmanager
def using_formats(cfg):
    order = str(cfg.get('concur_date_order', 'AUTO')).upper()
    number = str(cfg.get('concur_number_style', 'DOT')).upper()
    if order not in ('AUTO', 'YMD', 'MDY', 'DMY') or number not in ('DOT', 'COMMA'):
        raise ValueError('Concur 표시 서식 설정을 확인해 주세요.')
    a, b = DATE_ORDER.set(order), NUMBER_STYLE.set(number)
    try:
        yield
    finally:
        DATE_ORDER.reset(a)
        NUMBER_STYLE.reset(b)


def configured_run(function):
    @wraps(function)
    def run(*args, **kwargs):
        from . import settings
        cfg = signature(function).bind(*args, **kwargs).arguments.get('cfg')
        with using_formats(settings.load() if cfg is None else cfg):
            return function(*args, **kwargs)
    return run


def show_settings(app):
    import tkinter as tk
    from tkinter import ttk, messagebox
    from . import settings
    window = tk.Toplevel(app)
    window.title('Concur 표시 서식')
    window.transient(app)
    previous = window.grab_current()
    box = ttk.Frame(window, padding=16)
    box.pack(fill='both', expand=True)
    dates = {'자동 판단 (모호하면 보류)': 'AUTO', '년 / 월 / 일': 'YMD',
             '월 / 일 / 년': 'MDY', '일 / 월 / 년': 'DMY'}
    numbers = {'1,234.00 (소수점: 점)': 'DOT', '1.234,00 (소수점: 쉼표)': 'COMMA'}
    def selected(options, key, default):
        code = app.cfg.get(key, default)
        return next((label for label, value in options.items() if value == code), next(iter(options)))
    d = tk.StringVar(window, selected(dates, 'concur_date_order', 'AUTO'))
    n = tk.StringVar(window, selected(numbers, 'concur_number_style', 'DOT'))
    ttk.Label(box, text='Concur 계정에 실제로 표시되는 순서와 숫자 서식을 선택하세요.\n브라우저 언어와 날짜 순서는 다를 수 있습니다.').pack(anchor='w')
    for label, variable, options in [('날짜 순서', d, dates), ('금액 표시', n, numbers)]:
        ttk.Label(box, text=label).pack(anchor='w', pady=(10, 3))
        ttk.Combobox(box, textvariable=variable, values=list(options), state='readonly', width=32).pack(fill='x')
    ttk.Label(box, text='이 프로그램의 해석 기준만 바꿉니다. Concur 계정 설정은 바꾸지 않습니다.\n다른 계정으로 로그인할 때는 다시 확인하세요.', wraplength=430).pack(pady=10)
    def close():
        window.destroy()
        if previous is not None and previous.winfo_exists():
            previous.grab_set()
    def save():
        cfg = dict(app.cfg, concur_date_order=dates[d.get()], concur_number_style=numbers[n.get()])
        try:
            settings.save(cfg)
        except OSError as exc:
            messagebox.showerror('설정을 저장하지 못했습니다', str(exc), parent=window)
            return
        app.cfg.update(cfg)
        close()
    ttk.Button(box, text='저장', command=save).pack(side='right')
    ttk.Button(box, text='취소', command=close).pack(side='right', padx=8)
    window.protocol('WM_DELETE_WINDOW', close)
    window.bind('<Escape>', lambda event: close())
    window.grab_set()


def amount_normalizer_js(style=None):
    """Use the Python parser's grammar, retaining sign and decimal information."""
    from .concur_values import money_pattern
    style = style or NUMBER_STYLE.get()
    return r'''
      const integerAmount = (raw) => {
        if (raw == null) return null;
        let s = String(raw).normalize('NFKC')
          .replace(/[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g, '')
          .replace(/\u2212/g, '-').replace(/\s+/g, ' ').trim();
        s = s.replace(/KRW/gi, '').replace(/[₩원]/g, '').trim();
        let neg = false;
        if (s.startsWith('(') && s.endsWith(')')) { neg = true; s = s.slice(1,-1).trim(); }
        else if (s.endsWith('-')) { neg = true; s = s.slice(0,-1).trim(); }
        else if (/^[+-]/.test(s)) { neg = s[0] === '-'; s = s.slice(1).trim(); }
        if (!new RegExp(PATTERN).test(s)) return null;
        let integral = s.split(DECIMAL)[0].replace(/[,. ]/g, '').replace(/^0+(?=\d)/, '');
        return (neg && integral !== '0' ? '-' : '') + integral;
      };
    '''.replace('PATTERN', json.dumps('^(?:' + money_pattern(style) + ')$')).replace(
        'DECIMAL', json.dumps(',' if style == 'COMMA' else '.'))


def amount_check_js():
    return '(expected) => {' + amount_normalizer_js() + amount_normalizer_js('DOT').replace('integerAmount', 'nativeIntegerAmount') + '''
      const el = document.querySelector('#transactionAmount');
      return !!el && (el.type === 'number' ? nativeIntegerAmount(el.value) : integerAmount(el.value)) === String(expected);
    }'''


def room_check_js(read_script):
    return '(expected) => {' + amount_normalizer_js() + '''
      const cells = (''' + read_script + ''')();
      return cells.length === expected.length && cells.every((cell, i) =>
        integerAmount(cell.value) === String(expected[i]));
    }'''


def range_parts(value):
    from .concur_values import clean
    return re.split(r'\s+(?:-|–|—|~|to|至)\s+|\s*[~～]\s*', clean(value), flags=re.I)


def placeholder_order(value):
    from .concur_values import clean
    hit = re.search(r'(YYYY|MM|DD)\s*([/.-])\s*(YYYY|MM|DD)\s*\2\s*(YYYY|MM|DD)', clean(value).upper())
    if not hit:
        return None
    result = ''.join({'YYYY':'Y', 'MM':'M', 'DD':'D'}[hit[i]] for i in (1,3,4))
    return result if result in ('YMD','MDY','DMY') else None


def range_dates(value, placeholder=''):
    from .concur_values import resolve_dates
    parts = range_parts(value)
    if len(parts) != 2:
        return None
    values = resolve_dates([{'date': part} for part in parts], order=placeholder_order(placeholder))
    return tuple(values) if all(values) else None


def range_matches(value, start, end, placeholder=''):
    return range_dates(value, placeholder) == (start, end)


def format_range(start, end, current='', placeholder=''):
    """Use the field's numeric format or an explicit setting; no language guess."""
    from .concur_values import clean, date_options
    parts = range_parts(current)
    order, sep = None, '-'
    token = clean(parts[0]) if len(parts) == 2 else ''
    if token:
        hit = re.fullmatch(r'(\d{1,4})\s*([/.-])\s*(\d{1,2})\s*\2\s*(\d{1,4})\.?', token)
        resolved = range_dates(current, placeholder)
        if hit and resolved:
            order = date_options(token).get(resolved[0])
            sep = hit[2]
    if order not in ('YMD', 'MDY', 'DMY'):
        found = re.search(r'(YYYY|MM|DD)\s*([/.-])\s*(YYYY|MM|DD)\s*\2\s*(YYYY|MM|DD)', clean(placeholder).upper())
        if found:
            order = ''.join({'YYYY':'Y', 'MM':'M', 'DD':'D'}[found[i]] for i in (1,3,4))
            sep = found[2]
    explicit = DATE_ORDER.get()
    if order not in ('YMD', 'MDY', 'DMY') and explicit != 'AUTO':
        order, sep = explicit, '/' if explicit != 'YMD' else '-'
    if order not in ('YMD', 'MDY', 'DMY'):
        raise ValueError('숙박 날짜 입력 서식을 확인하지 못했습니다. Concur 표시 서식에서 날짜 순서를 선택해 주세요.')
    def render(d):
        data = {'Y': f'{d.year:04}', 'M': f'{d.month:02}', 'D': f'{d.day:02}'}
        return sep.join(data[c] for c in order)
    return render(start) + ' - ' + render(end)
