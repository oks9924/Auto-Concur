from pathlib import Path


def replace(path, old, new):
    p = Path(path)
    s = p.read_text(encoding='utf-8')
    if s.count(old) != 1:
        raise ValueError(f'Unexpected source: {path}: {old[:90]} ({s.count(old)})')
    p.write_text(s.replace(old, new), encoding='utf-8')

replace('src/gui.py', 'self.title("Auto-Concur · 작업 공간 UX 2026.09.17")',
        'self.title(f"Auto-Concur · 코드 {paths.stamp()}")')
replace('tests/test_calendar_ux.py', "('주차비','PARKG',0,False)", "('주차비','PARKG',1,True)")
replace('tests/test_calendar_ux.py', "'파일명':'a.pdf','경비유형':'주차비'", "'파일명':'a.pdf','경비유형':'렌터카비'")
replace('tests/test_sheet.py',
    '    # 코멘트 한 칸에 세 유형이 겹쳐 걸린다\n    assert len(칠한칸[get_column_letter(MANIFEST_COLUMNS.index("코멘트") + 1) + "2"]) == 3',
    '''    # User-confirmed description-only types were expanded; check the actual
    # formulas, not just a historical rule count.
    from src.expense_policy import DESCRIPTION_ONLY_TYPES
    types = {ATTENDEE_REQUIRED_TYPE, LODGING_TYPE, *DESCRIPTION_ONLY_TYPES}
    type_col = get_column_letter(MANIFEST_COLUMNS.index("경비유형") + 1)
    expected = {f'${type_col}2="{name}"' for name in types}
    actual = 칠한칸[get_column_letter(MANIFEST_COLUMNS.index("코멘트") + 1) + "2"]
    assert set(actual) == expected and len(actual) == len(expected)''')
replace('tests/test_sheet.py',
    '    for type_name in GREEN_BY_TYPE:\n        assert type_name in EXPENSE_TYPE_CODES, type_name',
    '''    from src.expense_policy import TRAINING_TYPE
    # A confirmed local guidance rule does not invent a Concur type code.
    pending = set(GREEN_BY_TYPE) - set(EXPENSE_TYPE_CODES)
    assert pending == {TRAINING_TYPE}
    assert GREEN_BY_TYPE[TRAINING_TYPE] == ['코멘트']''')

replace('tests/test_calendar_ux.py', '''def root():
    window = tk.Tk()
    window.geometry('900x650')
    yield window
    for job in window.tk.call('after', 'info'):
        window.after_cancel(job)
    window.destroy()''', '''def root(tk_window):
    tk_window.geometry('900x650')
    return tk_window''')
replace('tests/test_workspace_ux.py', '''def root():
    app=tk.Tk(); app.geometry('800x600'); app.update()
    yield app
    if app.winfo_exists():
        for task in app.tk.call('after','info'): app.after_cancel(task)
        app.destroy()''', '''def root(tk_window):
    tk_window.geometry('800x600'); tk_window.update()
    return tk_window''')
replace('tests/test_ready_update.py', '''def root():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()''', '''def root(tk_window):
    tk_window.withdraw()
    return tk_window''')
replace('tests/test_worksheet.py', '''@pytest.fixture(scope='module')
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()''', '''@pytest.fixture
def tk_root(tk_window):
    tk_window.withdraw()
    return tk_window''')
replace('tests/test_attendee_defaults.py', 'def editor(tmp_path):', 'def editor(tmp_path, tk_window):')
replace('tests/test_attendee_defaults.py', "root=tk.Tk(); root.geometry('900x700'); root.update()", "root=tk_window; root.geometry('900x700'); root.update()")
replace('tests/test_attendee_defaults.py', '''    if e.winfo_exists():
        if e.pending:
            e.after_cancel(e.pending)
            e.pending = None
        e.grab_release()
        e.destroy()
    # Descendant callbacks must be cleaned up by their owning widgets first.
    for task in root.tk.call('after', 'info'):
        root.after_cancel(task)
    root.destroy()''', '    # tk_window owns cleanup, including when editor construction fails.')
replace('tests/test_mileage.py', 'def editor(tmp_path,monkeypatch):', 'def editor(tmp_path,monkeypatch,tk_window):')
replace('tests/test_mileage.py', "root=tk.Tk();root.geometry('1200x900');root.update()", "root=tk_window;root.geometry('1200x900');root.update()")
replace('tests/test_mileage.py', '''    if e.winfo_exists():
        if e.pending: e.after_cancel(e.pending);e.pending=None
        e.destroy()
    for task in root.tk.call('after','info'): root.after_cancel(task)
    root.destroy()''', '    # tk_window owns cleanup, including when editor construction fails.')
replace('tests/test_attendee_favorites.py', '''def root(tmp_path,monkeypatch):
    monkeypatch.setattr(paths,'base',lambda:tmp_path)
    app=tk.Tk();app.geometry('800x600');app.update()
    yield app
    for child in list(app.winfo_children()): child.destroy()
    for task in app.tk.call('after','info'): app.after_cancel(task)
    app.destroy()''', '''def root(tmp_path,monkeypatch,tk_window):
    monkeypatch.setattr(paths,'base',lambda:tmp_path)
    tk_window.geometry('800x600');tk_window.update()
    return tk_window''')
replace('tests/test_attendee_favorites.py', 'def test_home_management_button_disabled_when_busy(root,monkeypatch,tmp_path):',
        'def test_home_management_button_disabled_when_busy(monkeypatch,tmp_path,tk_cleanup):')
replace('tests/test_attendee_favorites.py', '''    app=gui.App();app.update()
    button=next''', '''    monkeypatch.setattr(paths,'base',lambda:tmp_path)
    app=gui.App();tk_cleanup(app);app.update()
    button=next''')
replace('tests/test_attendee_favorites.py', "    for task in app.tk.call('after','info'):app.after_cancel(task)\n    app.destroy()", '    # Registered with tk_cleanup before any assertions.')
replace('tests/test_workspace_ux.py', 'def app(monkeypatch,tmp_path):', 'def app(monkeypatch,tmp_path,tk_cleanup):')
replace('tests/test_workspace_ux.py', '''    a=gui.App();a.update()
    yield a
    for task in a.tk.call('after','info'):a.after_cancel(task)
    a.destroy()''', '''    a=gui.App();tk_cleanup(a);a.update()
    yield a''')
replace('tests/test_display_formats.py', 'def test_format_settings_dialog_saves_without_touching_concur(monkeypatch):',
        'def test_format_settings_dialog_saves_without_touching_concur(monkeypatch,tk_cleanup):')
replace('tests/test_display_formats.py', '    app = tk.Tk()\n    app.withdraw()', '    app = tk.Tk()\n    tk_cleanup(app)\n    app.withdraw()')
replace('tests/test_display_formats.py', '    finally:\n        app.destroy()', '    finally:\n        pass  # tk_cleanup cancels callbacks with their original owners.')

p = Path('src/download_slips.py')
s = p.read_text(encoding='utf-8')
s = s.replace('from . import browser, console, paths, settings',
    'from . import browser, console, paths, settings\nfrom .download_diagnostics import DownloadWatch, save_bundle')
start = s.index('        page = browser.open_first(ctx, SLIP_PAGE)')
end = s.index('\n    print(f"받은 파일을 저장했습니다: {bundle}")', start)
body = s[start:end]
body = body.replace('        bundle = raw_dir / downloaded.suggested_filename\n        downloaded.save_as(bundle)\n        ctx.close()',
    '''        watch.mark('save_started')
        print('다운로드 시작을 확인했습니다. 파일 전송 완료 및 저장을 기다립니다...')
        bundle = save_bundle(downloaded, raw_dir)
        watch.mark('save_completed')
        watch.mark('normal_context_close')
        ctx.close()''')
body = body.replace('        page.evaluate("fnPdf()")', "        watch.mark('request_bundle')\n        page.evaluate(\"fnPdf()\")")
body = body.replace('        try:\n            with page.expect_download', "        watch.mark('await_download')\n        try:\n            with page.expect_download")
s = s[:start] + '        with DownloadWatch(ctx, out_dir) as watch:\n' + ''.join('    '+line+'\n' if line else '\n' for line in body.splitlines()) + s[end:]
p.write_text(s,encoding='utf-8')
