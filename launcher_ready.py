"""Windows 단일 EXE/폴더 배포의 진입점. --smoke-test는 빌드 검증 전용이다."""
import json
import sys
import traceback
from pathlib import Path


def smoke_test():
    import tempfile
    from datetime import date
    from src import gui, settings, organize
    from src.calendar_input import DateEntry, DatePicker
    from src.worksheet import write_json
    from src.worksheet_editor import Editor
    from src.stay_calendar import StayCalendar
    gui._preload = lambda: None
    app = gui.App()
    app.update()
    controls = []
    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, DateEntry):
                controls.append(child)
            walk(child)
    walk(app)
    assert len(controls) == 2
    controls[0].open()
    controls[0].dialog.select(date(2026,9,1))
    assert app.from_date.get() == '2026.09.01'
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp)/'workbook.json'
        row = {**dict.fromkeys(organize.MANIFEST_COLUMNS,''), '거래일':'2026-09-17',
               '금액':'1000','승인번호':'TEST','파일명':'test.pdf','가맹점명':'Test'}
        write_json(path, {'version':1,'rows':[row]})
        editor = Editor(app, path, settings.DEFAULTS)
        editor.table.select_cell(0,editor.COLUMNS.index('입실날짜'))
        editor.pick_stay_dates()
        dialog = next(w for w in editor.winfo_children() if isinstance(w, StayCalendar))
        dialog.start, dialog.end = date(2026,9,1), date(2026,9,3)
        dialog.apply()
        editor.sync()
        assert editor.model.rows[0]['퇴실날짜'] == '2026-09-03'
        if editor.pending:
            editor.after_cancel(editor.pending)
        editor.destroy()
    app.destroy()
    return {'smoke_test':'passed','main_calendars':2,'worksheet_calendar':True,'concur_contacted':False}


if __name__ == '__main__':
    home = Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).parent
    try:
        if '--smoke-test' in sys.argv:
            (home/'smoke-result.json').write_text(json.dumps(smoke_test(),indent=2),encoding='utf-8')
        else:
            from src.gui import main
            raise SystemExit(main())
    except Exception:
        detail = traceback.format_exc()
        (home/'startup-error.txt').write_text(detail,encoding='utf-8')
        if '--smoke-test' not in sys.argv:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Auto-Concur 실행 오류', '프로그램을 시작하지 못했습니다. 빌드한 실행파일과 사용 폴더의 쓰기 권한을 확인해 주세요.\n\n'+detail[-1600:])
            root.destroy()
        raise SystemExit(1)
