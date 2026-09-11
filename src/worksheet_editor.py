"""프로그램 안에서 편집·복사·붙여넣기·임시 저장·C단계 실행."""
from pathlib import Path
from copy import deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tksheet import Sheet

from . import sheet, settings
from .worksheet import Worksheet, normalize


class Editor(tk.Toplevel):
    COLUMNS = ['상태', '거래일', '금액', '가맹점명', *sheet.EDITABLE]

    def __init__(self, parent, source: Path, cfg: dict, on_run=None):
        model = Worksheet(source)
        super().__init__(parent)
        self.withdraw()
        self.cfg, self.model, self.on_run = dict(cfg), model, on_run
        self.pending = None
        self.loading = False
        self.title('경비 입력 · Auto-Concur')
        self.geometry('1240x680')
        self.minsize(980, 520)
        self.resizable(True, True)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        head = ttk.Frame(self, padding=(16, 14))
        head.grid(row=0, column=0, sticky='ew')
        title = ttk.Label(head, text='경비 입력', font=('맑은 고딕', 17, 'bold'))
        title.pack(anchor='w')
        title.bind('<Double-Button-1>', self.toggle_maximize)
        ttk.Label(head, text='빈칸은 현재 값을 유지합니다. 대중교통은 코멘트(설명)만 반영합니다. 영수증은 없을 때만 첨부합니다.').pack(anchor='w', pady=(5, 0))
        ttk.Label(head, text='Ctrl+C/V 복사·붙여넣기   ·   Ctrl+Z/Y 실행 취소·다시 실행   ·   Ctrl+S 저장   ·   Delete 선택 칸 비우기').pack(anchor='w', pady=(3, 0))

        tools = ttk.Frame(self, padding=(16, 0, 16, 10))
        tools.grid(row=1, column=0, sticky='ew')
        for label, command in [('복사', lambda: self.table.copy()), ('붙여넣기', lambda: self.table.paste()),
                               ('실행 취소', lambda: self.table.undo()), ('다시 실행', lambda: self.table.redo()),
                               ('여러 행에 입력', self.bulk), ('다시 불러오기', self.reload), ('이전 저장 복원', self.restore),
                               ('가져오기', self.import_file), ('내보내기', self.export_file)]:
            ttk.Button(tools, text=label, command=command).pack(side='left', padx=(0, 5))
        filters = ttk.Frame(self, padding=(16, 0, 16, 10))
        filters.grid(row=2, column=0, sticky='ew')
        ttk.Label(filters, text='검색').pack(side='left')
        self.query = tk.StringVar()
        ttk.Entry(filters, textvariable=self.query, width=32).pack(side='left', padx=(6, 14))
        self.filter = tk.StringVar(value='전체')
        ttk.Combobox(filters, textvariable=self.filter, values=['전체', '숙박비', '식음료', '입력 있음', '영수증만', '입력 확인'],
                     state='readonly', width=15).pack(side='left')
        self.count = ttk.Label(filters)
        self.count.pack(side='right')

        ttk.Label(filters, text='초록색: 유형별 입력 안내 (빈칸은 유지)').pack(side='left', padx=8)
        self.text_size = tk.StringVar(value='11')
        ttk.Combobox(filters, textvariable=self.text_size, values=['10', '11', '12', '14'],
                     state='readonly', width=3).pack(side='left')
        ttk.Label(filters, text='pt').pack(side='left')

        self.table = Sheet(self, headers=self.COLUMNS, theme='light blue',
                           font=('맑은 고딕', 11, 'normal'), header_font=('맑은 고딕', 11, 'bold'),
                           table_fg='#17212e', table_bg='#ffffff',
                           scrollbar_theme_inheritance='clam',
                           vertical_scroll_arrowsize=14, horizontal_scroll_arrowsize=14,
                           default_column_width=135, default_row_height=30,
                           paste_can_expand_x=False, paste_can_expand_y=False)
        self.table.grid(row=3, column=0, sticky='nsew', padx=16)
        self.table.enable_bindings('single_select', 'drag_select', 'column_select', 'row_select',
                                   'column_width_resize', 'row_height_resize', 'double_click_column_resize',
                                   'arrowkeys', 'copy', 'cut', 'paste', 'delete', 'undo', 'edit_cell',
                                   'right_click_popup_menu', 'rc_select', 'select_all', 'ctrl_select')
        self.table.readonly_columns([0, 1, 2, 3])
        self.table.highlight_columns([0, 1, 2, 3], bg='#f1f4f8')
        self.table.bind('<<SheetModified>>', self.modified)
        self.text_size.trace_add('write', lambda *a: self.resize_text())
        self.query.trace_add('write', lambda *a: self.apply_filter())
        self.filter.trace_add('write', lambda *a: self.apply_filter())
        foot = ttk.Frame(self, padding=16)
        foot.grid(row=4, column=0, sticky='ew')
        self.status = ttk.Label(foot, wraplength=650)
        self.status.pack(side='left', fill='x', expand=True)
        ttk.Button(foot, text='닫기', command=self.close).pack(side='right', padx=(8, 0))
        if on_run:
            ttk.Button(foot, text='저장하고 Concur 반영', command=self.run).pack(side='right', padx=(8, 0))
        ttk.Button(foot, text='저장', command=self.save).pack(side='right')
        self.bind('<Control-s>', lambda event: (self.save(), 'break')[1])
        recovered = False
        warning = ''
        try:
            recovered = self.model.recover_draft()
        except Exception as exc:
            warning = str(exc)
        self.refresh()
        self.apply_view()
        self.status.configure(text=warning or ('임시 저장 내용을 복원했습니다. 확인 후 저장하세요.' if recovered else '거래일·금액·가맹점은 원본 보호를 위해 수정할 수 없습니다.'))
        self.deiconify()
        self.grab_set()

    def toggle_maximize(self, event=None):
        self.state('normal' if self.state() == 'zoomed' else 'zoomed')
        return 'break'

    def apply_view(self):
        """열은 항상 전부 표시한다."""
        names = self.COLUMNS
        self.table.display_columns('all', redraw=False)
        widths = {'상태': 95, '거래일': 110, '금액': 100, '가맹점명': 180,
                  '경비유형': 200, '비즈니스목적': 180, '코멘트': 200, '참석자': 200, '추가 참석자': 200,
                  'Booking Channel': 170}
        for i, name in enumerate(names):
            self.table.column_width(i, widths.get(name, 125), redraw=False)
        self.table.set_xview(0)
        self.table.refresh()

    def resize_text(self):
        size = int(self.text_size.get())
        self.table.set_options(font=('맑은 고딕', size, 'normal'),
                               header_font=('맑은 고딕', size, 'bold'))
        self.table.set_all_row_heights(height=max(30, size * 3), redraw=True)

    def row_status(self, row):
        try:
            normalize(row, self.cfg)
        except (ValueError, sheet.SheetError):
            return '입력 확인'
        return '입력 있음' if any(row.get(key, '').strip() for key in sheet.EDITABLE) else '영수증만'

    def refresh(self):
        self.loading = True
        self.table.set_sheet_data([[self.row_status(row), *[row.get(c, '') for c in self.COLUMNS[1:]]]
                                  for row in self.model.rows])
        for name, choices in settings.choices(self.cfg).items():
            self.table.dropdown(self.table.span(None, self.COLUMNS.index(name), None, self.COLUMNS.index(name) + 1),
                                values=['', *choices], edit_data=False, state='normal', validate_input=False)
        self.table.reset_undos()
        self.loading = False
        self.apply_filter()
        self.highlight_inputs()

    def sync(self):
        data = self.table.get_sheet_data()
        self.model.replace_edits([dict(zip(self.COLUMNS, row)) for row in data])

    def modified(self, event=None):
        if self.loading:
            return
        self.sync()
        for i, row in enumerate(self.model.rows):
            self.table.set_cell_data(i, 0, self.row_status(row), redraw=False)
        self.highlight_inputs()
        self.table.refresh()
        if self.pending:
            self.after_cancel(self.pending)
        self.pending = self.after(800, self.autosave)
        self.status.configure(text='편집 중 · 잠시 후 임시 저장합니다. 저장된 입력만 C단계에 반영됩니다.')

    def highlight_inputs(self):
        self.table.dehighlight_cells(all_=True, redraw=False)
        for r, row in enumerate(self.model.rows):
            for name in sheet.GREEN_BY_TYPE.get(row.get('경비유형', '').strip(), []):
                self.table.highlight_cells(row=r, column=self.COLUMNS.index(name),
                                           bg='#d9efdc', fg='#173d22', redraw=False)
        self.table.refresh()

    def autosave(self):
        self.pending = None
        try:
            self.model.save_draft()
            self.status.configure(text='임시 저장했습니다. 입력 확인 후 저장하거나 Concur 반영을 누르세요.')
        except OSError as exc:
            self.status.configure(text=f'임시 저장 실패: {exc}')

    def apply_filter(self):
        if not hasattr(self, 'table'):
            return
        query, mode = self.query.get().casefold().strip(), self.filter.get()
        shown = []
        for i, row in enumerate(self.model.rows):
            status = self.row_status(row)
            text = ' '.join(str(row.get(c, '')) for c in self.COLUMNS[1:]).casefold()
            kind = row.get('경비유형', '')
            if query and query not in text:
                continue
            if mode in ('입력 있음', '영수증만', '입력 확인') and status != mode:
                continue
            if mode == '숙박비' and '숙박비' not in kind:
                continue
            if mode == '식음료' and '식음료' not in kind:
                continue
            shown.append(i)
        self.table.display_rows(rows=shown, all_rows_displayed=False, redraw=True)
        self.count.configure(text=f'{len(shown)} / {len(self.model.rows)}건 · 필터는 표시만 바꿉니다')

    def bulk(self):
        cells = self.table.get_selected_cells(get_rows=True, get_columns=True)
        selected = sorted({self.table.displayed_row_to_data(r) for r, c in cells})
        if not selected:
            messagebox.showinfo('행 선택', '먼저 입력할 행이나 셀 범위를 선택해 주세요.', parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title(f'선택한 {len(selected)}개 행에 입력')
        dialog.transient(self)
        box = ttk.Frame(dialog, padding=18)
        box.pack()
        name, value = tk.StringVar(value='코멘트'), tk.StringVar()
        ttk.Combobox(box, textvariable=name, values=sheet.EDITABLE, state='readonly', width=25).pack(fill='x')
        entry = ttk.Combobox(box, textvariable=value, width=60)
        entry.pack(fill='x', pady=10)
        name.trace_add('write', lambda *a: entry.configure(values=['', *settings.choices(self.cfg).get(name.get(), [])]))
        ttk.Label(box, text='기존 값도 바뀝니다. 빈 값은 해당 필드를 미입력으로 만듭니다.').pack()
        if self.cfg.get('attendee_default'):
            def fill_mine():
                name.set('참석자')
                value.set(self.cfg['attendee_default'])
            ttk.Button(box, text='설정한 내 참석자 불러오기', command=fill_mine).pack(pady=(8, 0))
        def finish():
            dialog.destroy()
            self.grab_set()
        def apply():
            column = self.COLUMNS.index(name.get())
            data = deepcopy(self.table.get_sheet_data())
            for r in selected:
                data[r][column] = value.get().strip()
            self.table.set_data(0, 0, data=data, undo=True, emit_event=True)
            finish()
        ttk.Button(box, text='선택 행에 적용', command=apply).pack(pady=(12, 0))
        dialog.protocol('WM_DELETE_WINDOW', finish)
        dialog.grab_set()

    def save(self):
        self.table.close_text_editor(set_data=True)
        self.sync()
        try:
            self.model.save(self.cfg)
        except Exception as exc:
            self.status.configure(text=str(exc))
            messagebox.showerror('입력 또는 저장 확인', str(exc), parent=self)
            return False
        if self.pending:
            self.after_cancel(self.pending)
            self.pending = None
        self.refresh()
        self.status.configure(text='저장했습니다. 엑셀을 열 필요 없이 C단계에 반영할 수 있습니다.')
        return True

    def run(self):
        if self.save():
            callback = self.on_run
            self.destroy()
            self.master.after_idle(callback)

    def restore(self):
        if self.model.dirty and not messagebox.askyesno('이전 저장 복원', '현재 편집을 이전 저장 값으로 바꿀까요?', parent=self):
            return
        try:
            self.model.restore_previous()
            self.refresh()
            self.modified()
        except Exception as exc:
            messagebox.showerror('복원 실패', str(exc), parent=self)

    def reload(self):
        if self.model.dirty and not messagebox.askyesno('다시 불러오기', '저장하지 않은 변경을 버릴까요?', parent=self):
            return
        try:
            if self.pending:
                self.after_cancel(self.pending)
                self.pending = None
            self.model.draft_path.unlink(missing_ok=True)
            self.model = Worksheet(self.model.target if self.model.target.exists() else self.model.source)
            self.refresh()
            self.status.configure(text='저장된 입력을 다시 불러왔습니다.')
        except Exception as exc:
            messagebox.showerror('불러오기 실패', str(exc), parent=self)

    def import_file(self):
        if self.model.dirty and not messagebox.askyesno('입력 가져오기', '현재 편집을 가져온 값으로 바꿀까요?', parent=self):
            return
        name = filedialog.askopenfilename(parent=self, title='입력 가져오기', filetypes=[('작업 데이터', '*.xlsx *.csv *.json')])
        if not name:
            return
        try:
            incoming = sheet.read_raw(Path(name))
            key = lambda row: (row.get('승인번호'), str(row.get('거래일', ''))[:10], row.get('금액') or row.get('합계'))
            mapping = {key(row): row for row in incoming}
            if len(mapping) != len(incoming) or any(key(row) not in mapping for row in self.model.rows):
                raise sheet.SheetError('현재 전표와 날짜·금액·승인번호가 일치하는 입력만 가져올 수 있습니다.')
            self.model.replace_edits([mapping[key(row)] for row in self.model.rows])
            self.refresh()
            self.modified()
        except Exception as exc:
            messagebox.showerror('가져오기 실패', str(exc), parent=self)

    def export_file(self):
        name = filedialog.asksaveasfilename(parent=self, title='현재 입력을 엑셀로 내보내기',
                                          defaultextension='.xlsx', filetypes=[('엑셀', '*.xlsx')])
        if name:
            self.table.close_text_editor(set_data=True)
            self.sync()
            try:
                sheet.write_xlsx(self.model.columns, self.model.rows, Path(name), settings.choices(self.cfg))
            except Exception as exc:
                messagebox.showerror('내보내기 실패', str(exc), parent=self)

    def close(self):
        self.table.close_text_editor(set_data=True)
        self.sync()
        if self.model.dirty:
            answer = messagebox.askyesnocancel('입력 저장', '변경한 내용을 저장하고 닫을까요?', parent=self)
            if answer is None or (answer and not self.save()):
                return
            if not answer:
                self.model.draft_path.unlink(missing_ok=True)
        if self.pending:
            self.after_cancel(self.pending)
        self.destroy()
