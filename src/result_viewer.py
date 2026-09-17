"""Read-only view of persisted verification history, never a fresh Concur check."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import tkinter as tk
from tkinter import ttk

STATES = {'verified': '당시 검증됨', 'needs_review': '확인 필요', 'running': '중단 여부 확인', 'pending': '실행 전 확인'}


@dataclass(frozen=True)
class Record:
    report: str
    expense: str
    kind: str
    state: str
    label: str
    message: str
    updated: str
    raw_updated: str


def load_records(path: Path) -> list[Record]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('version') != 1 or not isinstance(payload.get('tasks'), dict):
        raise ValueError('검증 기록 형식을 읽지 못했습니다. 원본 파일은 변경하지 않았습니다.')
    result = []
    for key, task in payload['tasks'].items():
        if not isinstance(key, str) or len(key.split('|')) != 4 or not isinstance(task, dict):
            raise ValueError('잘못된 작업 기록이 있습니다. 전체 로그를 확인하세요.')
        report, expense, kind, _ = key.split('|')
        report = report.rstrip('/').rsplit('/', 1)[-1]
        raw = str(task.get('updated', ''))
        try:
            instant = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            shown = instant.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z') if instant.tzinfo else raw + ' (시간대 미상)'
        except ValueError:
            shown = raw or '시간 미기록'
        state = STATES.get(str(task.get('state', '')), '상태 미확인')
        result.append(Record(report, expense, {'receipt': '영수증', 'edit': '경비 입력'}.get(kind, kind), state,
                             str(task.get('label', '')), str(task.get('message', '')), shown, raw))
    return sorted(result, key=lambda row: row.raw_updated, reverse=True)


class ResultViewer(ttk.Frame):
    def __init__(self, parent, folder):
        super().__init__(parent, padding=12)
        self.folder, self.records, self.visible = folder, [], []
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        self.note = ttk.Label(self, text='저장된 과거 검증 기록입니다. 현재 Concur 값이나 이번 실행의 성공 여부를 보장하지 않습니다.\n'
            '매칭 전 보류·취소·목록 탐색 오류는 이 기록에 없을 수 있으므로 상세 로그도 확인하세요.', wraplength=880)
        self.note.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        self.bind('<Configure>', lambda e: self.note.configure(wraplength=max(300, e.width - 30)))
        tools = ttk.Frame(self)
        tools.grid(row=1, column=0, sticky='ew')
        self.filter = tk.StringVar(value='전체')
        box = ttk.Combobox(tools, textvariable=self.filter, values=['전체', '확인 필요', '당시 검증됨'], state='readonly', width=14)
        box.pack(side='left')
        self.query = tk.StringVar()
        ttk.Label(tools, text='검색').pack(side='left', padx=(12, 5))
        ttk.Entry(tools, textvariable=self.query, width=22).pack(side='left')
        ttk.Button(tools, text='기록 새로 읽기', command=self.reload).pack(side='right')
        self.summary = ttk.Label(self, text='기록 새로 읽기를 눌러 선택한 전표 폴더의 기록을 확인하세요.', wraplength=800)
        self.summary.grid(row=2, column=0, sticky='ew', pady=8)
        frame = ttk.Frame(self)
        frame.grid(row=3, column=0, sticky='nsew')
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        columns = ('state', 'kind', 'label', 'report', 'updated')
        self.table = ttk.Treeview(frame, columns=columns, show='headings', selectmode='browse', height=8)
        for key, title, width in zip(columns, ('기록 상태', '작업', '대상', '리포트 ID', '기록 시각 (이 PC 기준)'), (130, 90, 340, 170, 190)):
            self.table.heading(key, text='기록 상태' if key == 'state' else title)
            self.table.column(key, width=width, minwidth=80, stretch=key == 'label')
        self.table.grid(row=0, column=0, sticky='nsew')
        ys = ttk.Scrollbar(frame, orient='vertical', command=self.table.yview)
        xs = ttk.Scrollbar(frame, orient='horizontal', command=self.table.xview)
        ys.grid(row=0, column=1, sticky='ns'); xs.grid(row=1, column=0, sticky='ew')
        self.table.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.table.tag_configure('review', background='#fff2d9')
        self.details = tk.Text(self, height=5, state='disabled', wrap='word')
        self.details.grid(row=4, column=0, sticky='ew', pady=(8, 4))
        ttk.Button(self, text='선택 기록 복사', command=self.copy_selected).grid(row=5, column=0, sticky='e')
        self.filter.trace_add('write', lambda *a: self.refresh())
        self.query.trace_add('write', lambda *a: self.refresh())
        self.table.bind('<<TreeviewSelect>>', self.select)

    def reload(self):
        self.records = []
        self.path = self.folder() / 'concur-progress.json'
        try:
            self.records = load_records(self.path)
            self.refresh()
        except (OSError, ValueError) as exc:
            self.refresh()
            self.summary.configure(text=f'기록 읽기 실패: {exc}')

    def refresh(self):
        query, kind = self.query.get().strip().casefold(), self.filter.get()
        self.visible = [r for r in self.records if
            (kind == '전체' or (r.state == '당시 검증됨' if kind == '당시 검증됨' else r.state != '당시 검증됨'))
            and (not query or query in ' '.join((r.report, r.expense, r.label, r.message, r.state)).casefold())]
        self.table.delete(*self.table.get_children())
        for i, r in enumerate(self.visible):
            self.table.insert('', 'end', iid=str(i), values=(r.state, r.kind, r.label, r.report, r.updated),
                              tags=() if r.state == '당시 검증됨' else ('review',))
        count = sum(r.state != '당시 검증됨' for r in self.records)
        self.summary.configure(text=f'과거 작업 기록 {len(self.records)}개 · 확인 필요 {count}개 · 현재 표시 {len(self.visible)}개'
            if self.records else '저장된 검증 기록이 없습니다. 작업을 실행하지 않았거나, 기록 전 중단됐을 수 있습니다.')
        self.set_details('기록을 선택하면 이유와 경비 ID를 볼 수 있습니다. 서로 다른 입력 내용의 이력은 별도 작업 기록입니다.')

    def set_details(self, text):
        self.details.configure(state='normal'); self.details.delete('1.0', 'end')
        self.details.insert('end', text); self.details.configure(state='disabled')

    def select(self, event=None):
        selected = self.table.selection()
        if selected:
            r = self.visible[int(selected[0])]
            self.set_details(f'{r.label}\n상태: {r.state} · 리포트: {r.report} · 경비 ID: {r.expense}\n'
                             f'기록 시각: {r.updated}\n이유: {r.message or "별도 메시지 없음"}')

    def copy_selected(self):
        if self.table.selection():
            self.clipboard_clear(); self.clipboard_append(self.details.get('1.0', 'end-1c'))
