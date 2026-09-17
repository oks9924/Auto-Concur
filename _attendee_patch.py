from pathlib import Path

def replace(path,old,new):
    p=Path(path);text=p.read_text(encoding='utf-8')
    if text.count(old)!=1: raise RuntimeError(f'Unexpected base: {path}: {old[:100]}')
    p.write_text(text.replace(old,new),encoding='utf-8')

replace('src/worksheet_editor.py', 'from .worksheet import Worksheet, normalize',
    'from .worksheet import Worksheet, normalize\nfrom .attendee_defaults import AttendeeDefaults')
replace('src/worksheet_editor.py', "        self.table.bind('<<SheetModified>>', self.modified)",
    "        self.attendee_defaults = AttendeeDefaults(self)\n        self.table.bind('<<SheetModified>>', self.modified)")
replace('src/worksheet_editor.py', '    def modified(self, event=None):\n        if self.loading:\n            return\n        self.sync()',
    '    def modified(self, event=None):\n        if self.loading:\n            return\n        filled = self.attendee_defaults.apply(event)\n        self.sync()')
replace('src/worksheet_editor.py', "        self.status.configure(text='편집 중 · 잠시 후 임시 저장합니다. 저장된 입력만 C단계에 반영됩니다.')",
    "        self.status.configure(text=(f'내부 직원간 식음료 {filled}건의 빈 참석자에 기본 참석자를 채웠습니다. Ctrl+Z로 함께 되돌릴 수 있습니다.' if filled else '편집 중 · 잠시 후 임시 저장합니다. 저장된 입력만 C단계에 반영됩니다.'))")
replace('src/row_editor.py', 'from .worksheet import normalize',
    'from .worksheet import normalize\nfrom .attendee_defaults import attendee_on_type_change')
replace('src/row_editor.py', "        self.variables['경비유형'].trace_add('write', lambda *a: self.update_guide())",
    "        self.previous_type = self.variables['경비유형'].get()\n        self.variables['경비유형'].trace_add('write', self.type_changed)")
replace('src/row_editor.py', '    def update_guide(self):',
    '''    def type_changed(self, *args):
        after = self.values()
        before = {**after, '경비유형': self.previous_type}
        self.previous_type = after['경비유형']
        value = attendee_on_type_change(before, after, self.editor.cfg)
        if value is not None:
            self.variables['참석자'].set(value)
        self.update_guide()

    def update_guide(self):''')
replace('src/row_editor.py', '        self.editor.table.set_data(0, 0, data=data, undo=True, emit_event=True)',
    '''        # The form already applied the default at type selection. Respect a
        # name the user subsequently cleared or replaced before applying.
        with self.editor.attendee_defaults.pause():
            self.editor.table.set_data(0, 0, data=data, undo=True, emit_event=True)''')
replace('tests/test_attendee_defaults.py',
    "    for task in root.tk.call('after','info'): root.after_cancel(task)\n    root.destroy()",
    "    if e.winfo_exists():\n        if e.pending:\n            e.after_cancel(e.pending)\n            e.pending = None\n        e.grab_release()\n        e.destroy()\n    # Descendant callbacks must be cleaned up by their owning widgets first.\n    for task in root.tk.call('after', 'info'):\n        root.after_cancel(task)\n    root.destroy()")
