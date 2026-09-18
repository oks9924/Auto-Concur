"""Shared desktop calendar: month/year jump, range preview and keyboard navigation.

Interaction reference: W3C APG date-picker dialog; this is Tk, not an ARIA widget.
No web view or additional runtime dependency is required.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
import tkinter as tk
from tkinter import ttk


def shift_month(value: date, delta: int) -> date:
    number = max(12, min(9999 * 12 + 11, value.year * 12 + value.month - 1 + delta))
    year, month = divmod(number, 12)
    month += 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def date_text(value, separator='-'):
    return (f'{value.year:04d}{separator}{value.month:02d}{separator}{value.day:02d}'
            if value else '')


def period_preset(name, today=None):
    today = today or date.today()
    if name == '오늘':
        return today, today
    if name == '최근 7일':
        return today - timedelta(days=6), today
    if name == '이번 달':
        return today.replace(day=1), today
    if name == '지난달':
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    raise ValueError(f'알 수 없는 조회 기간: {name}')


class CalendarPanel(ttk.Frame):
    """Calendar grid with persistent day widgets.

    Month navigation and range changes update existing buttons instead of destroying
    and rebuilding 42/84 Tk widgets, which is noticeably smoother on Windows.
    """
    def __init__(self, parent, initial, on_select, months=1):
        super().__init__(parent)
        self.cursor = initial or date.today()
        self.year, self.month = self.cursor.year, self.cursor.month
        self.on_select, self.months = on_select, max(1, int(months))
        self.start = self.end = None
        self.year_var = tk.StringVar(self, str(self.year))
        self.month_var = tk.StringVar(self, str(self.month))
        nav = ttk.Frame(self)
        nav.pack(fill='x', pady=(0, 8))
        ttk.Button(nav, text='이전 달', command=lambda: self.move(-1)).pack(side='left')
        self.year_entry = ttk.Spinbox(nav, from_=1, to=9999, textvariable=self.year_var,
                                      width=6, command=self.jump)
        self.year_entry.pack(side='left', padx=(10, 2))
        ttk.Label(nav, text='년').pack(side='left')
        self.month_entry = ttk.Combobox(nav, values=list(range(1, 13)), width=3,
                                        textvariable=self.month_var, state='readonly')
        self.month_entry.pack(side='left', padx=(8, 2))
        ttk.Label(nav, text='월').pack(side='left')
        ttk.Button(nav, text='다음 달', command=lambda: self.move(1)).pack(side='right')
        self.year_entry.bind('<Return>', self.jump)
        self.year_entry.bind('<FocusOut>', self.jump)
        self.month_entry.bind('<<ComboboxSelected>>', self.jump)
        self.body = ttk.Frame(self)
        self.body.pack()
        self.buttons = {}
        self._slots = []
        self._ensure_slots(self.months)
        self.draw()

    def _ensure_slots(self, count):
        while len(self._slots) < count:
            offset = len(self._slots)
            box = ttk.Frame(self.body, padding=(4, 0), style='Calendar.TFrame')
            box.grid(row=0, column=offset, sticky='n')
            title = ttk.Label(box, anchor='center', style='Calendar.TLabel',
                              font=('맑은 고딕', 11, 'bold'))
            title.grid(row=0, column=0, columnspan=7, pady=(0, 7))
            for col, name in enumerate('월화수목금토일'):
                ttk.Label(box, text=name, anchor='center', style='Calendar.TLabel').grid(
                    row=1, column=col, pady=4)
            cells = []
            for index in range(42):
                row, col = divmod(index, 7)
                button = tk.Button(
                    box, text='', width=4, pady=3, font=('맑은 고딕', 10),
                    bg='#ffffff', fg='#1f2937', activebackground='#d7e8f2',
                    activeforeground='#14212e', relief='flat', borderwidth=0,
                    highlightthickness=2, highlightbackground='#ffffff',
                    highlightcolor='#245eab', cursor='hand2', takefocus=0,
                )
                button._calendar_value = None
                button.configure(command=lambda b=button: self.select(b._calendar_value)
                                 if b._calendar_value is not None else None)
                for key in ('Left', 'Right', 'Up', 'Down', 'Home', 'End',
                            'Prior', 'Next', 'Return', 'space'):
                    button.bind('<'+key+'>',
                        lambda event, b=button: self.key(event, b._calendar_value)
                        if b._calendar_value is not None else 'break')
                button.grid(row=row + 2, column=col, padx=1, pady=1, sticky='nsew')
                cells.append(button)
            self._slots.append({'box': box, 'title': title, 'cells': cells})

    def jump(self, event=None):
        try:
            year, month = int(self.year_var.get()), int(self.month_var.get())
            value = date(year, month, min(self.cursor.day, calendar.monthrange(year, month)[1]))
        except (ValueError, OverflowError):
            self.year_var.set(str(self.year))
            self.month_var.set(str(self.month))
            return 'break'
        self.show(value, focus=event is not None and event.type == tk.EventType.KeyPress)
        return 'break'

    def show(self, value, focus=False):
        self.cursor = value
        self.year, self.month = value.year, value.month
        self.draw()
        if focus:
            self.focus_day()

    def move(self, delta):
        self.show(shift_month(date(self.year, self.month, min(self.cursor.day,
            calendar.monthrange(self.year, self.month)[1])), delta))

    def set_selection(self, start, end=None):
        if (self.start, self.end) == (start, end):
            return
        self.start, self.end = start, end
        self.paint_selection()

    def day_colors(self, value, col=None):
        col = value.weekday() if col is None else col
        edge = value in (self.start, self.end)
        inside = self.start and self.end and self.start < value < self.end
        bg = '#14634b' if edge else '#e2f1eb' if inside else '#ffffff'
        fg = '#ffffff' if edge else '#a52834' if col == 6 else '#1f2937'
        return bg, fg

    def paint_selection(self):
        for value, button in self.buttons.items():
            bg, fg = self.day_colors(value)
            button.configure(bg=bg, fg=fg, highlightbackground=bg)

    def focus_day(self):
        button = self.buttons.get(self.cursor)
        if button:
            button.focus_set()

    def select(self, value):
        if value is None:
            return
        self.cursor = value
        self.on_select(value)

    def key(self, event, value):
        if value is None:
            return 'break'
        key, delta = event.keysym, None
        try:
            if key in ('Return', 'space'):
                self.select(value)
                return 'break'
            if key in ('Left', 'Right', 'Up', 'Down'):
                delta = {'Left': -1, 'Right': 1, 'Up': -7, 'Down': 7}[key]
            elif key == 'Home':
                delta = -value.weekday()
            elif key == 'End':
                delta = 6 - value.weekday()
            elif key in ('Prior', 'Next'):
                value = shift_month(value, (-1 if key == 'Prior' else 1) *
                                    (12 if event.state & 1 else 1))
            else:
                return None
            if delta is not None:
                value += timedelta(days=delta)
            self.show(value, focus=True)
        except (ValueError, OverflowError):
            pass
        return 'break'

    def draw(self):
        self.year_var.set(str(self.year))
        self.month_var.set(str(self.month))
        self._ensure_slots(self.months)
        self.buttons.clear()
        first = date(self.year, self.month, 1)
        today = date.today()
        for offset, slot in enumerate(self._slots):
            if offset >= self.months:
                slot['box'].grid_remove()
                continue
            shown = shift_month(first, offset)
            if offset and shown <= first:
                slot['box'].grid_remove()
                continue
            slot['box'].grid()
            slot['title'].configure(text=f'{shown.year}년 {shown.month}월')
            weeks = calendar.Calendar(firstweekday=0).monthdayscalendar(shown.year, shown.month)
            days = [day for week in weeks for day in week]
            days += [0] * (42 - len(days))
            for index, day in enumerate(days[:42]):
                button = slot['cells'][index]
                if not day:
                    button._calendar_value = None
                    button.configure(text='', state='disabled', takefocus=0,
                                     bg='#ffffff', fg='#1f2937',
                                     highlightbackground='#ffffff', cursor='')
                    continue
                value = date(shown.year, shown.month, day)
                button._calendar_value = value
                bg, fg = self.day_colors(value, index % 7)
                button.configure(
                    text=f'{day}·' if value == today else str(day),
                    state='normal', takefocus=int(value == self.cursor),
                    bg=bg, fg=fg, highlightbackground=bg, cursor='hand2')
                self.buttons[value] = button


class CalendarDialog(tk.Toplevel):
    """Restore the exact prior focus/grab. Cancel never mutates caller data."""
    def __init__(self, parent, title):
        self.previous_grab = parent.grab_current()
        self.previous_focus = parent.focus_get()
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(False, False)
        self.configure(background='#ffffff')
        style = ttk.Style(self)
        style.configure('Calendar.TFrame', background='#ffffff')
        style.configure('Calendar.TLabel', background='#ffffff', foreground='#243245')
        style.configure('Calendar.TRadiobutton', background='#ffffff', padding=0)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Escape>', lambda event: (self.close(), 'break')[1])
        self.bind('<Tab>', self.tab)
        self.bind('<Shift-Tab>', lambda event: self.tab(event, True))
        self.bind('<ISO_Left_Tab>', lambda event: self.tab(event, True))

    def tab(self, event, backwards=False):
        widget = self.focus_get()
        if widget is not None:
            target = widget.tk_focusPrev() if backwards else widget.tk_focusNext()
            if target is not None:
                target.focus_set()
        return 'break'

    def present(self, calendar_panel):
        def style_children(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Frame):
                    child.configure(style='Calendar.TFrame')
                elif isinstance(child, ttk.Label):
                    child.configure(style='Calendar.TLabel')
                elif isinstance(child, ttk.Radiobutton):
                    child.configure(style='Calendar.TRadiobutton')
                style_children(child)
        style_children(self)
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if self.winfo_reqwidth() > sw - 32 and calendar_panel.months > 1:
            calendar_panel.months = 1
            calendar_panel.draw()
            self.update_idletasks()
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        parent = self.master.winfo_toplevel()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f'+{max(0, min(x, sw-width-12))}+{max(0, min(y, sh-height-36))}')
        self.deiconify()
        self.grab_set()
        calendar_panel.focus_day()

    def close(self):
        if not self.winfo_exists():
            return
        self.grab_release()
        self.destroy()
        for widget, action in ((self.previous_grab, 'grab_set'), (self.previous_focus, 'focus_set')):
            try:
                if widget is not None and widget.winfo_exists():
                    getattr(widget, action)()
            except tk.TclError:
                pass
