"""기준 소스에 달력·목록 준비 수정 적용. 생성된 배포 ZIP에는 적용 완료 소스가 들어간다."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def replace(path, before, after):
    target = ROOT / path
    source = target.read_text(encoding='utf-8')
    if after in source:
        return
    if source.count(before) != 1:
        raise RuntimeError(f'기준 소스 불일치: {path}: {before[:70]!r}')
    target.write_text(source.replace(before, after), encoding='utf-8')


def main():
    replace('src/gui.py', 'from . import console, paths, retry, settings',
        'from . import console, paths, retry, settings\nfrom .calendar_input import DateEntry, initial_period, checked_period')
    replace('src/gui.py', 'self.title("Concur 경비 자동화")',
        'self.title("Auto-Concur · 달력 개선판 2026.09.17")')
    replace('src/gui.py', 'self.from_date = tk.StringVar(value="2026.08.01")\n        self.to_date = tk.StringVar(value="2026.08.31")',
        'first, last = initial_period(self.cfg)\n        self.from_date = tk.StringVar(value=first)\n        self.to_date = tk.StringVar(value=last)')
    replace('src/gui.py', 'ttk.Entry(span, textvariable=self.from_date, width=12).pack(side="left")',
        'DateEntry(span, self.from_date, "조회 시작일 선택").pack(side="left")')
    replace('src/gui.py', 'ttk.Entry(span, textvariable=self.to_date, width=12).pack(side="left")',
        'DateEntry(span, self.to_date, "조회 종료일 선택").pack(side="left")')
    replace('src/gui.py', '        settings.save(self.cfg)',
        '        self.cfg["period_from"] = self.from_date.get()\n        self.cfg["period_to"] = self.to_date.get()\n        settings.save(self.cfg)')
    replace('src/gui.py', '        from_date, to_date, limit = self.from_date.get(), self.to_date.get(), self._limit()',
        '        try:\n            from_date, to_date = checked_period(self.from_date.get(), self.to_date.get())\n        except ValueError as exc:\n            messagebox.showerror("조회 기간 확인", str(exc), parent=self)\n            return\n        limit = self._limit()')
    replace('src/worksheet_editor.py', "('숙박 날짜', self.pick_stay_dates)", "('숙박 날짜 · 달력', self.pick_stay_dates)")
    replace('src/worksheet_editor.py', 'Ctrl+C/V 복사·붙여넣기   ·   Ctrl+Z/Y 실행 취소·다시 실행   ·   Ctrl+S 저장   ·   Delete 선택 칸 비우기',
        '입실·퇴실 칸 더블클릭 또는 Enter: 달력   ·   Ctrl+C/V 복사·붙여넣기   ·   Ctrl+Z/Y 실행 취소·다시 실행   ·   Ctrl+S 저장')
    replace('src/worksheet_editor.py', '    def pick_stay_dates(self):',
        '    def pick_stay_dates(self, row_index=None, date_column=None):')
    replace('src/worksheet_editor.py', "        if not selected:\n            messagebox.showinfo('행 선택', '날짜를 입력할 경비 행을 먼저 선택해 주세요.', parent=self)",
        "        if row_index is None and not selected:\n            messagebox.showinfo('행 선택', '날짜를 입력할 경비 행을 먼저 선택해 주세요.', parent=self)")
    replace('src/worksheet_editor.py', '        row_index = self.table.displayed_row_to_data(selected.row)',
        '        if row_index is None:\n            row_index = self.table.displayed_row_to_data(selected.row)')
    replace('src/worksheet_editor.py', "        StayCalendar(self, anchor, sheet._as_date(row.get('입실날짜'), anchor.year),\n                     sheet._as_date(row.get('퇴실날짜'), anchor.year), apply)",
        "        dialog = StayCalendar(self, anchor, sheet._as_date(row.get('입실날짜'), anchor.year),\n                              sheet._as_date(row.get('퇴실날짜'), anchor.year), apply)\n        if date_column == '퇴실날짜':\n            dialog.mode.set('퇴실')")
    replace('src/worksheet_editor.py', "        if self.COLUMNS[column] in sheet.DATE_COLUMNS and event.key == '??':\n            self.after_idle(self.pick_stay_dates)\n            return None",
        "        if self.COLUMNS[column] in sheet.DATE_COLUMNS:\n            row = self.table.displayed_row_to_data(event.row)\n            name = self.COLUMNS[column]\n            self.after_idle(lambda: self.pick_stay_dates(row, name))\n            return None")
    replace('src/stay_calendar.py', '        self.grab_set()\n',
        "        self.grab_set()\n        self.bind('<Escape>', lambda event: self.close())\n")
    replace('src/attach_receipts.py', 'from .concur_ui import ConcurUIError as AttachError',
        'from .concur_ui import ConcurUIError as AttachError\nfrom .concur_values import resolve_dates, parse_amount, amount_from_summary\nfrom .concur_rows import rows_complete, readiness_detail')
    target = ROOT / 'src/attach_receipts.py'
    source = target.read_text(encoding='utf-8')
    if 'READ_ROWS_JS = (' in source:
        a = source.index('READ_ROWS_JS = (')
        b = source.index('# 행을 읽지 못했을 때 남길 근거.', a)
        source = source[:a] + 'from .concur_rows import READ_ROWS_JS\n\n' + source[b:]
        target.write_text(source, encoding='utf-8')
    replace('src/attach_receipts.py', '    receipt_file: str = ""  # 붙어 있는 영수증 파일 이름 (화면이 알려준다)',
        '    receipt_file: str = ""  # 붙어 있는 영수증 파일 이름 (화면이 알려준다)\n    read_problem: str = ""')
    source = target.read_text(encoding='utf-8')
    start = source.index('def amount_from_label(')
    end = source.index('\ndef print_unreadable(', start)
    source = source[:start] + '''def amount_from_label(label: str) -> int | None:
    return amount_from_summary(label)


def _parse_amount(text: str) -> int | None:
    return parse_amount(text)

''' + source[end:]
    start = source.index('def read_rows(')
    end = source.index('\ndef rows_when_ready(', start)
    source = source[:start] + '''def read_rows(page) -> list[Row]:
    raw_rows = _eval(page, READ_ROWS_JS)
    dates = resolve_dates(raw_rows)
    rows = []
    for raw, when in zip(raw_rows, dates):
        amount = _parse_amount(raw.get("amount", ""))
        backup = amount_from_label(raw.get("label", ""))
        if amount is None and not str(raw.get("amount", "")).strip():
            amount = backup
        elif amount is not None and backup is not None and amount != backup:
            amount = None
        label = " ".join(x for x in (raw.get("expenseType", ""), raw.get("vendor", ""), raw.get("amount", "")) if x)
        rows.append(Row(index=raw["index"], when=when, amount=amount,
                        text=label[:80], expense_id=raw.get("id"),
                        expense_type=raw.get("expenseType", ""), vendor=raw.get("vendor", ""),
                        has_receipt=raw.get("receipt"), raw_date=raw.get("date", ""),
                        raw_amount=raw.get("amount", "") or raw.get("label", ""),
                        receipt_file=raw.get("receiptFile", ""), read_problem=raw.get("readProblem", "")))
    return rows

''' + source[end:]
    target.write_text(source, encoding='utf-8')
    replace('src/attach_receipts.py', 'def rows_when_ready(page, tries: int = 30, wait_ms: int = 500)',
        'def rows_when_ready(page, tries: int = 90, wait_ms: int = 500)')
    replace('src/attach_receipts.py', 'complete = bool(rows) and all(r.expense_id and r.when and r.amount is not None for r in rows)',
        'complete = rows_complete(rows)')
    replace('src/attach_receipts.py', '        previous = signature if complete else None\n        page.wait_for_timeout(wait_ms)',
        '        previous = signature if complete else None\n        if _ in (10, 30, 60):\n            print("  목록 확인 중: " + readiness_detail(rows))\n        page.wait_for_timeout(wait_ms)')
    replace('src/attach_receipts.py', '    raise AttachError("경비 목록이 아직 완전히 준비되지 않았습니다. 일부 행만으로 매칭하지 않았습니다."\n                      + concur_ui.diagnose(page, "경비 목록 준비"))',
        '    raise AttachError("경비 목록을 안전하게 읽지 못했습니다. 일부 행만으로 매칭하지 않았습니다.\\n"\n                      + readiness_detail(rows) + concur_ui.diagnose(page, "경비 목록 준비"))')
    replace('src/attach_receipts.py', 'bad = [r for r in rows if not (r.when and r.amount)]',
        'bad = [r for r in rows if r.when is None or r.amount is None]')
    replace('src/attach_receipts.py', 'dated = [r for r in rows if r.when and r.amount and r.expense_id]',
        'dated = [r for r in rows if r.when is not None and r.amount is not None and r.expense_id]')
    replace('src/report_session.py', 'complete = bool(rows) and all(r.expense_id and r.when and r.amount is not None for r in rows)',
        'complete = ar.rows_complete(rows)')
    replace('src/report_session.py', '    while time.monotonic() < deadline:',
        '    list_since, list_key, last_note = None, None, 0\n    while time.monotonic() < deadline:')
    replace('src/report_session.py', '            complete = ar.rows_complete(rows)',
        '            now = time.monotonic()\n            if key != list_key:\n                list_since, list_key = (now if key else None), key\n            if key and now - last_note >= 10:\n                print("  목록 확인: " + ar.readiness_detail(rows))\n                last_note = now\n            complete = ar.rows_complete(rows)\n            if list_since is not None and now - list_since >= 45 and not complete:\n                raise ar.AttachError("경비 목록을 안전하게 읽지 못했습니다. " + ar.readiness_detail(rows)\n                                     + ar.concur_ui.diagnose(page, "경비 목록 준비"))')
    replace('src/browser.py', '    if not want:\n        return CHANNELS',
        '    if not want:\n        return ["msedge", "chrome", None] if getattr(sys, "frozen", False) else CHANNELS')
    print('달력과 경비 목록 읽기 수정 적용 완료')


if __name__ == '__main__':
    main()
