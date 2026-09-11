"""입실·퇴실을 함께 선택하고 검증하는 달력. 적용 전에는 원본을 변경하지 않는다."""
import calendar
from datetime import date
import tkinter as tk
from tkinter import ttk


class StayCalendar(tk.Toplevel):
    def __init__(self, parent, anchor, start, end, on_apply):
        super().__init__(parent)
        self.title('입실·퇴실 날짜 선택')
        self.transient(parent)
        self.resizable(False, False)
        self.start, self.end, self.on_apply = start, end, on_apply
        shown = start or anchor
        self.year, self.month = shown.year, shown.month
        self.mode = tk.StringVar(value='입실')
        box = ttk.Frame(self, padding=16)
        box.pack()
        ttk.Label(box, text='입실일을 고른 뒤 퇴실일을 선택하세요.').pack(anchor='w')
        modes = ttk.Frame(box)
        modes.pack(fill='x', pady=8)
        for name in ('입실', '퇴실'):
            ttk.Radiobutton(modes, text=name, value=name, variable=self.mode).pack(side='left', padx=12)
        nav = ttk.Frame(box)
        nav.pack(fill='x')
        ttk.Button(nav, text='◀', width=4, command=lambda: self.move(-1)).pack(side='left')
        self.month_label = ttk.Label(nav, anchor='center', width=24)
        self.month_label.pack(side='left', expand=True)
        ttk.Button(nav, text='▶', width=4, command=lambda: self.move(1)).pack(side='right')
        self.grid_box = ttk.Frame(box)
        self.grid_box.pack(pady=8)
        self.selection = ttk.Label(box, width=48, anchor='center')
        self.selection.pack(pady=8)
        self.error = ttk.Label(box, foreground='#b42318', wraplength=380)
        self.error.pack()
        buttons = ttk.Frame(box)
        buttons.pack(fill='x', pady=(8, 0))
        ttk.Button(buttons, text='두 날짜 비우기', command=self.clear).pack(side='left')
        ttk.Button(buttons, text='취소', command=self.close).pack(side='right')
        ttk.Button(buttons, text='적용', command=self.apply).pack(side='right', padx=8)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.draw()
        self.grab_set()

    def move(self, delta):
        number = self.year * 12 + self.month - 1 + delta
        y, m = divmod(number, 12)
        if 1 <= y <= 9999:
            self.year, self.month = y, m + 1
            self.draw()

    def choose(self, day):
        selected = date(self.year, self.month, day)
        if self.mode.get() == '입실':
            self.start = selected
            self.mode.set('퇴실')
        else:
            self.end = selected
        self.error.configure(text='')
        self.draw()

    def clear(self):
        self.start = self.end = None
        self.mode.set('입실')
        self.draw()

    def draw(self):
        self.month_label.configure(text=f'{self.year}년 {self.month}월')
        for child in self.grid_box.winfo_children():
            child.destroy()
        for c, name in enumerate('월화수목금토일'):
            ttk.Label(self.grid_box, text=name, anchor='center').grid(row=0, column=c)
        for r, week in enumerate(calendar.monthcalendar(self.year, self.month), 1):
            for c, day in enumerate(week):
                if not day:
                    continue
                value = date(self.year, self.month, day)
                selected = value in (self.start, self.end)
                inside = self.start and self.end and self.start < value < self.end
                button = tk.Button(self.grid_box, text=str(day), width=4,
                    bg='#b9dfc3' if selected else '#e9f4ec' if inside else '#ffffff',
                    command=lambda d=day: self.choose(d))
                button.grid(row=r, column=c, padx=2, pady=2)
        nights = f' · {(self.end - self.start).days}박' if self.start and self.end and self.end > self.start else ''
        self.selection.configure(text=f'입실 {self.start or "미선택"} → 퇴실 {self.end or "미선택"}{nights}')

    def apply(self):
        if bool(self.start) != bool(self.end) or (self.start and self.end <= self.start):
            self.error.configure(text='입실·퇴실을 함께 선택하고, 퇴실일은 입실일보다 뒤로 선택해 주세요.')
            return
        self.on_apply(self.start.isoformat() if self.start else '', self.end.isoformat() if self.end else '')
        self.close()

    def close(self):
        self.destroy()
        self.master.grab_set()
