"""One expense as a form. Apply changes once to the table; never save to Concur."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, messagebox
from . import sheet, settings
from .worksheet import normalize
from .expense_policy import input_guide
from .calendar_input import DateEntry
from .stay_calendar import StayCalendar
from .ui_scroll import ScrollArea


class RowEditor(tk.Toplevel):
    def __init__(self, editor, row_index):
        super().__init__(editor)
        self.editor, self.row_index = editor, row_index
        self.source = deepcopy(editor.model.rows[row_index])
        self.previous_grab = self.grab_current()
        self.previous_focus = self.focus_get()
        self.configure(background='#f5f7fb')
        self.title(f'경비 한 건 편집 · 원본 {row_index + 1}행')
        self.transient(editor)
        width = min(720, max(540, self.winfo_screenwidth() - 50))
        height = min(760, max(440, self.winfo_screenheight() - 90))
        self.geometry(f'{width}x{height}')
        self.minsize(min(width, 580), min(height, 460))
        self.columnconfigure(0, weight=1); self.rowconfigure(1, weight=1)
        head = ttk.Frame(self, padding=12)
        head.grid(row=0, column=0, sticky='ew')
        self.identity = ttk.Label(head, text=f"{self.source.get('거래일', '')} · {self.source.get('금액') or self.source.get('합계', '')}원\n"
            f"{self.source.get('가맹점명', '')}  (원본 정보는 수정하지 않습니다)", wraplength=650)
        self.identity.pack(anchor='w')
        head.bind('<Configure>', lambda e: self.identity.configure(wraplength=max(250, e.width - 30)))
        self.guide = ttk.Label(head, wraplength=650)
        self.guide.pack(anchor='w', pady=(5, 0))
        self.scroll = ScrollArea(self)
        self.scroll.grid(row=1, column=0, sticky='nsew', padx=8)
        self.variables, self.texts, self.labels = {}, {}, {}
        choices = settings.choices(editor.cfg)
        groups = [('기본 정보', sheet.EDITABLE[:3]), ('참석자', sheet.EDITABLE[3:5]), ('숙박 정보', sheet.LODGING_COLUMNS)]
        for title, fields in groups:
            group = ttk.LabelFrame(self.scroll.body, text=title, padding=10)
            group.pack(fill='x', pady=5)
            group.columnconfigure(1, weight=1)
            for i, name in enumerate(fields):
                label = ttk.Label(group, text=name)
                label.grid(row=i, column=0, sticky='nw', padx=(0, 12), pady=6)
                self.labels[name] = label
                value = str(self.source.get(name, ''))
                if name in ('비즈니스목적', '코멘트'):
                    widget = tk.Text(group, height=3, width=30, wrap='word', undo=True)
                    widget.insert('1.0', value)
                    self.texts[name] = widget
                else:
                    var = tk.StringVar(self, value)
                    self.variables[name] = var
                    if name in sheet.DATE_COLUMNS:
                        widget = DateEntry(group, var, name, separator='-')
                    elif name in choices:
                        widget = ttk.Combobox(group, textvariable=var, values=['', *choices[name]], state='readonly')
                    else:
                        widget = ttk.Entry(group, textvariable=var)
                widget.grid(row=i, column=1, sticky='ew', pady=6)
            if title == '숙박 정보':
                ttk.Button(group, text='입실·퇴실 함께 선택', command=self.calendar).grid(row=len(fields), column=1, sticky='w')
        self.variables['경비유형'].trace_add('write', lambda *a: self.update_guide())
        self.update_guide()
        foot = ttk.Frame(self, padding=12)
        foot.grid(row=2, column=0, sticky='ew'); foot.columnconfigure(0, weight=1)
        self.error = ttk.Label(foot, text='표에 적용한 뒤 작업지에서 저장하세요. Concur에는 아직 반영되지 않습니다.', wraplength=650)
        self.error.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        foot.bind('<Configure>', lambda event: self.error.configure(wraplength=max(260, event.width - 30)))
        ttk.Button(foot, text='취소', command=self.cancel).grid(row=1, column=0, sticky='e', padx=8)
        ttk.Button(foot, text='표에 적용', command=self.apply, style='UX.Primary.TButton').grid(row=1, column=1, sticky='e')
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda event: self.cancel())
        self.bind('<Control-s>', lambda event: (self.apply(), 'break')[1])
        self.scroll.enable_children()
        self.grab_set()

    def values(self):
        return {**{k: v.get() for k, v in self.variables.items()},
                **{k: v.get('1.0', 'end-1c') for k, v in self.texts.items()}}

    def update_guide(self):
        kind = self.variables['경비유형'].get()
        fields, known = input_guide(kind, self.editor.cfg.get('expense_type_codes', {}).get(kind))
        self.guide.configure(text=('초록 항목: 유형별 입력 안내 (필수 확정 아님)' if known else '안내 미등록 유형 · 필수 항목은 Concur에서 확인하세요.')
                             + '\n빈칸은 Concur의 기존 값을 유지합니다.')
        for name, label in self.labels.items():
            label.configure(text=name + (' · 입력 안내' if name in fields else ''),
                            foreground='#17603b' if name in fields else '#253447')

    def calendar(self):
        anchor = sheet._as_date(self.source.get('거래일'))
        def apply(start, end):
            self.variables['입실날짜'].set(start); self.variables['퇴실날짜'].set(end)
        StayCalendar(self, anchor, sheet._as_date(self.variables['입실날짜'].get(), anchor.year),
                     sheet._as_date(self.variables['퇴실날짜'].get(), anchor.year), apply)

    def apply(self):
        try:
            value = normalize({**self.source, **self.values()}, self.editor.cfg)
        except (ValueError, sheet.SheetError) as exc:
            self.error.configure(text=str(exc), foreground='#a12b22')
            return False
        current = self.editor.model.rows[self.row_index]
        if current != self.source:
            self.error.configure(text='편집 중 원본 행이 변경됐습니다. 취소하고 다시 열어 주세요.', foreground='#a12b22')
            return False
        data = deepcopy(self.editor.table.get_sheet_data())
        for name in sheet.EDITABLE:
            data[self.row_index][self.editor.COLUMNS.index(name)] = value.get(name, '')
        self.editor.table.set_data(0, 0, data=data, undo=True, emit_event=True)
        self.finish()
        return True

    def cancel(self):
        if any(str(self.source.get(k, '')).strip() != v.strip() for k, v in self.values().items()):
            if not messagebox.askyesno('편집 취소', '이 창의 변경을 버리고 닫을까요? 작업지의 다른 행은 바뀌지 않습니다.', parent=self):
                return
        self.finish()

    def finish(self):
        self.destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            self.previous_grab.grab_set()
        if self.previous_focus is not None and self.previous_focus.winfo_exists():
            self.previous_focus.focus_set()
