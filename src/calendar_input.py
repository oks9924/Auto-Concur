"""추가 라이브러리 없는 조회기간 달력 입력."""
import calendar
from datetime import date
import tkinter as tk
from tkinter import ttk
from .date_input import parse_date


class DatePicker(tk.Toplevel):
    def __init__(self, parent, initial, on_select, title='날짜 선택'):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.on_select = on_select
        self.selected = initial or date.today()
        self.year, self.month = self.selected.year, self.selected.month
        self.previous_grab = self.grab_current()
        frame = ttk.Frame(self, padding=14)
        frame.pack()
        nav = ttk.Frame(frame)
        nav.pack(fill='x')
        ttk.Button(nav, text='◀', width=4, command=lambda: self.move(-1)).pack(side='left')
        self.heading = ttk.Label(nav, width=24, anchor='center')
        self.heading.pack(side='left', expand=True)
        ttk.Button(nav, text='▶', width=4, command=lambda: self.move(1)).pack(side='right')
        self.days = ttk.Frame(frame)
        self.days.pack(pady=10)
        actions = ttk.Frame(frame)
        actions.pack(fill='x')
        ttk.Button(actions, text='오늘', command=lambda: self.select(date.today())).pack(side='left')
        ttk.Button(actions, text='취소', command=self.close).pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda event: self.close())
        self.bind('<Prior>', lambda event: self.move(-1))
        self.bind('<Next>', lambda event: self.move(1))
        self.draw()
        self.grab_set()
        self.focus_set()

    def move(self, step):
        y, m = divmod(self.year * 12 + self.month - 1 + step, 12)
        if 1 <= y <= 9999:
            self.year, self.month = y, m + 1
            self.draw()

    def draw(self):
        self.heading.configure(text=f'{self.year}년 {self.month}월')
        for widget in self.days.winfo_children():
            widget.destroy()
        for c, text in enumerate('월화수목금토일'):
            ttk.Label(self.days, text=text, anchor='center').grid(row=0, column=c)
        for r, week in enumerate(calendar.monthcalendar(self.year, self.month), 1):
            for c, day in enumerate(week):
                if day:
                    value = date(self.year, self.month, day)
                    label = f'[{day}]' if value == self.selected else str(day)
                    ttk.Button(self.days, text=label, width=4,
                               command=lambda d=value: self.select(d)).grid(row=r, column=c, padx=2, pady=2)

    def select(self, value):
        self.on_select(value)
        self.close()

    def close(self):
        previous = self.previous_grab
        self.destroy()
        if previous is not None and previous.winfo_exists():
            previous.grab_set()


class DateEntry(ttk.Frame):
    def __init__(self, parent, variable, title='날짜 선택'):
        super().__init__(parent)
        self.variable, self.picker_title = variable, title
        self.dialog = None
        self.entry = ttk.Entry(self, textvariable=variable, width=12, state='readonly', cursor='hand2')
        self.entry.pack(side='left')
        self.button = ttk.Button(self, text='달력', width=5, command=self.open)
        self.button.pack(side='left', padx=(3, 0))
        for key in ('<Button-1>', '<Return>', '<space>', '<Alt-Down>'):
            self.entry.bind(key, self.open)

    def open(self, event=None):
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.lift()
            return 'break'
        self.dialog = DatePicker(self, parse_date(self.variable.get()),
                                 lambda value: self.variable.set(value.strftime('%Y.%m.%d')),
                                 self.picker_title)
        return 'break'


def initial_period(cfg, today=None):
    today = today or date.today()
    start, end = parse_date(cfg.get('period_from')), parse_date(cfg.get('period_to'))
    if not start or not end or start > end:
        start, end = today.replace(day=1), today
    return start.strftime('%Y.%m.%d'), end.strftime('%Y.%m.%d')


def checked_period(start, end):
    first, last = parse_date(start), parse_date(end)
    if first is None or last is None:
        raise ValueError('시작일과 종료일을 달력에서 선택해 주세요.')
    if first > last:
        raise ValueError('종료일은 시작일과 같거나 뒤여야 합니다.')
    return first.strftime('%Y.%m.%d'), last.strftime('%Y.%m.%d')
