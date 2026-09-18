"""Unified date and date-range inputs; typing and calendar selection both work."""
from datetime import date, timedelta
import tkinter as tk
from tkinter import ttk
from .date_input import parse_date
from .calendar_widgets import CalendarDialog, CalendarPanel, date_text, period_preset

HELP = '방향키: 날짜 · Enter: 선택 · Esc: 취소\nPageUp/Down: 월 · Shift+PageUp/Down: 연도'


class DatePicker(CalendarDialog):
    def __init__(self, parent, initial, on_select, title='날짜 선택'):
        super().__init__(parent, title)
        self.on_select, self.selected = on_select, initial or date.today()
        box = ttk.Frame(self, padding=14)
        box.pack()
        self.panel = CalendarPanel(box, self.selected, self.select)
        self.panel.pack()
        self.panel.set_selection(self.selected)
        ttk.Label(box, text='· 오늘   /   선택한 날짜: '+date_text(self.selected)).pack(pady=(6, 3))
        ttk.Label(box, text=HELP, wraplength=360, foreground='#475569').pack()
        bar = ttk.Frame(box)
        bar.pack(fill='x', pady=(10, 0))
        ttk.Button(bar, text='오늘 선택', command=lambda: self.select(date.today())).pack(side='left')
        ttk.Button(bar, text='취소', command=self.close).pack(side='right')
        self.present(self.panel)

    @property
    def year(self):
        return self.panel.year

    @property
    def month(self):
        return self.panel.month

    def move(self, step):
        self.panel.move(step)

    def draw(self):
        self.panel.draw()

    def select(self, value):
        self.on_select(value)
        self.close()


class DateEntry(ttk.Frame):
    def __init__(self, parent, variable, title='날짜 선택', separator='.', year=None):
        super().__init__(parent)
        self.variable, self.picker_title, self.separator, self.year = variable, title, separator, year
        self.dialog = None
        self.entry = ttk.Entry(self, textvariable=variable, width=12)
        self.entry.grid(row=0, column=0)
        self.button = ttk.Button(self, text='달력', width=5, command=self.open)
        self.button.grid(row=0, column=1, padx=(3, 0))
        self.error = ttk.Label(self, text='', foreground='#b42318')
        self.entry.bind('<Return>', self.normalize)
        self.entry.bind('<FocusOut>', self.normalize)
        for key in ('<Double-Button-1>', '<Alt-Down>', '<F4>'):
            self.entry.bind(key, self.open)
        self._trace = self.variable.trace_add('write', self._clear_error)
        self.bind('<Destroy>', self._cleanup, add='+')

    def _cleanup(self, event):
        if event.widget is self:
            self.variable.trace_remove('write', self._trace)

    def _clear_error(self, *args):
        self.entry.state(['!invalid'])
        self.error.grid_remove()

    def normalize(self, event=None):
        text = self.variable.get().strip()
        value = parse_date(text, self.year)
        if text and value is None:
            self.entry.state(['invalid'])
            self.error.configure(text='날짜 확인: YYYY-MM-DD')
            self.error.grid(row=1, column=0, columnspan=2, sticky='w')
        elif value:
            self.variable.set(date_text(value, self.separator))
        return 'break' if event is not None and event.type == tk.EventType.KeyPress else None

    def open(self, event=None):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return 'break'
        self.dialog = DatePicker(self, parse_date(self.variable.get(), self.year),
            lambda value: self.variable.set(date_text(value, self.separator)), self.picker_title)
        return 'break'


class RangePicker(CalendarDialog):
    """Draft-only range editor. The caller receives values only after Apply."""
    def __init__(self, parent, anchor, start, end, on_apply, *, stay=False):
        super().__init__(parent, '입실·퇴실 날짜 선택' if stay else '조회 기간 선택')
        self.anchor, self.on_apply, self.stay = anchor or date.today(), on_apply, stay
        self._start, self._end = start, end
        self.labels = ('입실', '퇴실') if stay else ('시작', '종료')
        self.mode = tk.StringVar(self, self.labels[0])
        self.start_var, self.end_var = tk.StringVar(self, date_text(start)), tk.StringVar(self, date_text(end))
        box = ttk.Frame(self, padding=14)
        box.pack()
        fields = ttk.Frame(box)
        fields.pack(fill='x', pady=(0, 8))
        for label, variable in zip(self.labels, (self.start_var, self.end_var)):
            part = ttk.Frame(fields)
            part.pack(side='left', padx=(0, 12))
            ttk.Radiobutton(part, text=label+'일', value=label, variable=self.mode).pack(anchor='w')
            entry = ttk.Entry(part, textvariable=variable, width=16)
            entry.pack()
            entry.bind('<Return>', self.manual)
            entry.bind('<FocusOut>', self.manual)
            entry.bind('<FocusIn>', lambda event, name=label: self.mode.set(name))
        if not stay:
            shortcuts = ttk.Frame(box)
            shortcuts.pack(fill='x', pady=(0, 8))
            for name in ('오늘', '최근 7일', '이번 달', '지난달'):
                ttk.Button(shortcuts, text=name, command=lambda n=name: self.preset(n)).pack(side='left', padx=(0, 4))
        # One month keeps first-open latency low on Windows/dual-monitor setups.
        # Previous/next month buttons still cover cross-month ranges.
        months = 1
        self.panel = CalendarPanel(box, start or self.anchor, self.choose_date, months=months)
        self.panel.pack()
        self.selection = ttk.Label(box, anchor='center', font=('맑은 고딕', 10, 'bold'))
        self.selection.pack(pady=(8, 4))
        self.error = ttk.Label(box, foreground='#b42318', wraplength=360)
        self.help_label = ttk.Label(box, text='· 오늘  /  '+HELP+'\n날짜 입력: YYYY-MM-DD (연도 포함)',
                  wraplength=650 if months == 2 else 360, foreground='#475569')
        self.help_label.pack(pady=(4, 0))
        bar = self.bar = ttk.Frame(box)
        bar.pack(fill='x', pady=(10, 0))
        if stay:
            ttk.Button(bar, text='두 날짜 비우기', command=self.clear).pack(side='left')
        ttk.Button(bar, text='취소', command=self.close).pack(side='right')
        ttk.Button(bar, text='적용', command=self.apply).pack(side='right', padx=8)
        self.draw()
        self.present(self.panel)

    def show_error(self, text):
        self.error.configure(text=text)
        if text:
            self.help_label.pack_forget()
            self.error.pack(before=self.bar, pady=4)
        else:
            self.error.pack_forget()
            self.help_label.pack(before=self.bar, pady=(4, 0))

    @property
    def start(self):
        return self._start

    @start.setter
    def start(self, value):
        self._start = value
        self.start_var.set(date_text(value))

    @property
    def end(self):
        return self._end

    @end.setter
    def end(self, value):
        self._end = value
        self.end_var.set(date_text(value))

    @property
    def year(self):
        return self.panel.year

    @property
    def month(self):
        return self.panel.month

    def move(self, delta):
        self.panel.move(delta)

    def choose(self, day):
        self.choose_date(date(self.year, self.month, day))

    def choose_date(self, value):
        if self.mode.get() == self.labels[0]:
            self.start = value
            if self.end and (self.end < value or self.stay and self.end == value):
                self.end = None
            self.mode.set(self.labels[1])
        else:
            self.end = value
        self.show_error('')
        self.draw()

    def manual(self, event=None):
        values = []
        for label, variable in zip(self.labels, (self.start_var, self.end_var)):
            raw = variable.get().strip()
            value = parse_date(raw, self.anchor.year)
            if raw and value is None:
                self.show_error(f'{label}일 형식을 확인하세요. 예: 2026-09-17')
                return False
            values.append(value)
        self.start, self.end = values
        self.show_error('')
        if event is None or event.type != tk.EventType.FocusOut:
            self.draw()
        return True

    def preset(self, name):
        if self.stay:
            if name == '거래일로 이동':
                self.panel.show(self.anchor)
                return
            if not self.manual():
                return
            start = self.start or self.anchor
            try:
                end = start + timedelta(days=int(name[:-1]))
            except (ValueError, OverflowError):
                self.show_error('선택 가능한 날짜 범위를 벗어났습니다.')
                return
            self.start, self.end = start, end
        else:
            self.start, self.end = period_preset(name)
        self.panel.show(self.start)
        self.draw()

    def clear(self):
        self.start = self.end = None
        self.mode.set(self.labels[0])
        self.show_error('')
        self.draw()

    def draw(self):
        self.panel.set_selection(self.start, self.end)
        days = (self.end-self.start).days if self.start and self.end else None
        suffix = (f' · {days}박' if self.stay else f' · {days+1}일') if days is not None and days >= 0 else ''
        self.selection.configure(text=f'{self.labels[0]} {date_text(self.start) or "미선택"} → '
                                      f'{self.labels[1]} {date_text(self.end) or "미선택"}{suffix}')

    def apply(self):
        if not self.manual():
            return
        valid_empty = self.stay and self.start is None and self.end is None
        valid_range = self.start and self.end and (self.end > self.start if self.stay else self.end >= self.start)
        if not (valid_empty or valid_range):
            self.show_error(('입실·퇴실을 함께 선택하고, 퇴실일은 입실일보다 뒤로 선택해 주세요.'
                if self.stay else '시작일·종료일을 선택하고, 종료일은 시작일 이후로 선택해 주세요.'))
            return
        self.on_apply(date_text(self.start), date_text(self.end))
        self.close()


def initial_period(cfg, today=None):
    today = today or date.today()
    start, end = parse_date(cfg.get('period_from')), parse_date(cfg.get('period_to'))
    if not start or not end or start > end:
        start, end = today.replace(day=1), today
    return date_text(start, '.'), date_text(end, '.')


def checked_period(start, end):
    first, last = parse_date(start), parse_date(end)
    if first is None or last is None:
        raise ValueError('시작일과 종료일을 입력하거나 달력에서 선택해 주세요. 예: 2026.09.17')
    if first > last:
        raise ValueError('종료일은 시작일과 같거나 뒤여야 합니다.')
    return date_text(first, '.'), date_text(last, '.')
