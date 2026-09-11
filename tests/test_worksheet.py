from copy import deepcopy

import pytest

from src import sheet, settings, update_concur, organize
from src.worksheet import Worksheet, write_json


@pytest.fixture
def source(tmp_path):
    rows = [{**dict.fromkeys(organize.MANIFEST_COLUMNS, ''), '거래일': f'2026-08-0{i}',
             '금액': str(i * 1000), '승인번호': str(i), '파일명': f'{i}.pdf', '가맹점명': f'가게 {i}'}
            for i in (1, 2, 3)]
    path = tmp_path / 'workbook.json'
    write_json(path, {'version': 1, 'rows': rows})
    return path


def test_native_save_and_c_plan_without_excel(source):
    model = Worksheet(source)
    model.update(0, {'코멘트': '팀 회의'})
    model.save(settings.DEFAULTS)
    assert sheet.load(source)[0].comment == '팀 회의'
    assert update_concur.pick_sheet(source.parent, None) == source
    assert not list(source.parent.glob('*.xlsx'))


def test_blank_values_stay_blank_despite_defaults(source):
    model = Worksheet(source)
    model.save({**settings.DEFAULTS, 'attendee_default': 'do.not.add'})
    assert all(not row.attendee and not row.location and not row.channel for row in sheet.load(source))


def test_invalid_dates_do_not_overwrite_saved_data(source):
    model = Worksheet(source)
    previous = source.read_bytes()
    rows = deepcopy(model.rows)
    rows[0]['입실날짜'] = '8/10'
    model.replace_edits(rows)
    with pytest.raises(sheet.SheetError, match='함께'):
        model.save(settings.DEFAULTS)
    assert source.read_bytes() == previous


def test_invalid_dropdown_does_not_silently_fall_back(source):
    model = Worksheet(source)
    model.update(0, {'경비유형': '틀린 유형'})
    with pytest.raises(sheet.SheetError, match='목록에 없는'):
        model.save(settings.DEFAULTS)


def test_draft_recovers_partial_input_without_touching_original(source):
    model = Worksheet(source)
    rows = deepcopy(model.rows)
    rows[0]['입실날짜'] = '8/2'
    model.replace_edits(rows)
    model.save_draft()
    restored = Worksheet(source)
    assert restored.recover_draft()
    assert restored.rows[0]['입실날짜'] == '8/2'
    assert sheet.load(source)[0].checkin is None


def test_external_change_blocks_save_and_draft_recovery(source):
    first, other = Worksheet(source), Worksheet(source)
    first.update(0, {'코멘트': 'draft'})
    first.save_draft()
    other.update(0, {'코멘트': 'external'})
    other.save(settings.DEFAULTS)
    with pytest.raises(sheet.SheetError, match='변경'):
        first.save(settings.DEFAULTS)


def test_previous_save_can_be_restored(source):
    model = Worksheet(source)
    model.update(0, {'코멘트': 'first'})
    model.save(settings.DEFAULTS)
    model.update(0, {'코멘트': 'second'})
    model.save(settings.DEFAULTS)
    model.restore_previous()
    assert model.dirty and model.rows[0]['코멘트'] == 'first'
    assert sheet.load(source)[0].comment == 'second'  # 복원은 저장 전 초안이다.


def test_original_transaction_data_cannot_be_edited(source):
    model = Worksheet(source)
    rows = deepcopy(model.rows)
    rows[0].update({'금액': '999', '승인번호': 'wrong', '코멘트': 'allowed'})
    model.replace_edits(rows)
    assert model.rows[0]['금액'] == '1000' and model.rows[0]['승인번호'] == '1'
    assert model.rows[0]['코멘트'] == 'allowed'


def test_native_edits_survive_b_rerun(source):
    model = Worksheet(source)
    model.update(0, {'코멘트': 'keep'})
    model.save(settings.DEFAULTS)
    assert organize._kept_edits(source.parent)['1']['코멘트'] == 'keep'


@pytest.fixture(scope='module')
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def editor(source, monkeypatch, tk_root):
    import tkinter as tk
    from src.worksheet_editor import Editor
    root = tk_root
    monkeypatch.setattr(tk.Toplevel, 'deiconify', lambda self: None)
    window = Editor(root, source, settings.DEFAULTS)
    root.update()
    yield window
    if window.pending:
        window.after_cancel(window.pending)
    window.destroy()


def test_multiple_cell_paste_and_undo(editor, monkeypatch):
    table = editor.table
    monkeypatch.setattr(table.MT, 'clipboard_get', lambda: '목적1\t코멘트1\n목적2\t코멘트2')
    table.select_cell(0, editor.COLUMNS.index('비즈니스목적'))
    table.paste()
    editor.update()
    editor.sync()
    assert editor.model.rows[0]['코멘트'] == '코멘트1'
    assert editor.model.rows[1]['비즈니스목적'] == '목적2'
    table.undo()
    editor.update()
    editor.sync()
    assert editor.model.rows[0]['코멘트'] == editor.model.rows[1]['비즈니스목적'] == ''
    table.redo()
    editor.update()
    editor.sync()
    assert editor.model.rows[1]['코멘트'] == '코멘트2'


def test_paste_does_not_change_readonly_amount(editor, monkeypatch):
    monkeypatch.setattr(editor.table.MT, 'clipboard_get', lambda: '999999')
    editor.table.select_cell(0, editor.COLUMNS.index('금액'))
    editor.table.paste()
    editor.update()
    assert editor.table.get_sheet_data()[0][2] == '1000'


def test_filtered_paste_updates_correct_original_row(editor, monkeypatch):
    editor.query.set('가게 2')
    assert editor.table.display_rows() == [1]
    monkeypatch.setattr(editor.table.MT, 'clipboard_get', lambda: 'only second')
    editor.table.select_cell(0, editor.COLUMNS.index('코멘트'))
    editor.table.paste()
    editor.update()
    editor.sync()
    assert editor.model.rows[0]['코멘트'] == ''
    assert editor.model.rows[1]['코멘트'] == 'only second'


def test_program_editor_saves_native_data(editor):
    editor.table.set_cell_data(0, editor.COLUMNS.index('코멘트'), 'program input')
    assert editor.save()
    assert sheet.load(editor.model.target)[0].comment == 'program input'


def test_copy_uses_tab_separated_text(editor, monkeypatch):
    clipboard = []
    monkeypatch.setattr(editor.table.MT, 'clipboard_clear', lambda: None)
    monkeypatch.setattr(editor.table.MT, 'clipboard_append', clipboard.append)
    editor.table.select_row(0)
    editor.table.copy()
    assert len(clipboard) == 1 and '\t1000\t' in clipboard[0] and '가게 1' in clipboard[0]


def test_save_and_run_calls_parent_only_after_successful_save(editor):
    called = []
    editor.on_run = lambda: called.append('run')
    # destroy는 테스트 fixture에서 한다.
    original_destroy = editor.destroy
    editor.destroy = lambda: None
    try:
        editor.table.set_cell_data(0, editor.COLUMNS.index('코멘트'), 'before run')
        editor.run()
        editor.update()
        assert called == ['run']
        assert sheet.load(editor.model.target)[0].comment == 'before run'
    finally:
        editor.destroy = original_destroy


def test_b_creates_native_data_without_excel(tmp_path):
    (tmp_path / '20260901-1000.png').write_bytes(b'placeholder')
    assert organize.organize(tmp_path, True) == 0
    assert (tmp_path / 'workbook.json').exists()
    assert not (tmp_path / 'manifest.xlsx').exists()
    assert sheet.load(tmp_path / 'workbook.json')[0].amount == 1000


def test_b_preview_writes_nothing(tmp_path):
    (tmp_path / '20260901-1000.png').write_bytes(b'placeholder')
    assert organize.organize(tmp_path, False) == 0
    assert not (tmp_path / 'workbook.json').exists()
    assert not (tmp_path / 'manifest.csv').exists()


def test_all_columns_paste_and_undo(editor, monkeypatch):
    editor.table.set_cell_data(0, editor.COLUMNS.index('코멘트'), 'keep')
    assert editor.table.display_columns() == list(range(len(editor.COLUMNS)))
    monkeypatch.setattr(editor.table.MT, 'clipboard_get', lambda: '2026-09-01\t2026-09-03')
    editor.table.select_cell(0, editor.COLUMNS.index('입실날짜'))
    editor.table.paste()
    editor.update()
    editor.sync()
    assert editor.model.rows[0]['입실날짜'] == '2026-09-01'
    assert editor.model.rows[0]['퇴실날짜'] == '2026-09-03'
    assert editor.model.rows[0]['코멘트'] == 'keep'
    editor.table.undo()
    editor.sync()
    assert editor.model.rows[0]['입실날짜'] == ''
    assert editor.model.rows[0]['코멘트'] == 'keep'


def test_type_highlights_follow_excel_and_clear_previous_type(editor):
    editor.table.set_cell_data(0, editor.COLUMNS.index('경비유형'), '숙박비')
    editor.modified()
    date_cell = (0, editor.COLUMNS.index('입실날짜'))
    attendee_cell = (0, editor.COLUMNS.index('참석자'))
    assert 'highlight' in editor.table.MT.cell_options[date_cell]
    editor.table.set_cell_data(0, editor.COLUMNS.index('경비유형'), '내부 직원간 식음료')
    editor.modified()
    assert 'highlight' not in editor.table.MT.cell_options.get(date_cell, {})
    assert 'highlight' in editor.table.MT.cell_options[attendee_cell]
    assert editor.model.rows[0]['참석자'] == ''


def test_font_size_change_preserves_edits(editor):
    editor.table.set_cell_data(0, editor.COLUMNS.index('코멘트'), 'keep')
    editor.text_size.set('14')
    editor.sync()
    assert editor.table.font()[1] == 14
    assert editor.model.rows[0]['코멘트'] == 'keep'


def test_transit_highlights_only_description(editor):
    editor.table.set_cell_data(0, editor.COLUMNS.index('경비유형'), sheet.ATTENDEE_REQUIRED_TYPE)
    editor.modified()
    editor.table.set_cell_data(0, editor.COLUMNS.index('경비유형'), sheet.TRANSIT_TYPE)
    editor.modified()
    highlighted = [name for name in sheet.EDITABLE if 'highlight' in
                   editor.table.MT.cell_options.get((0, editor.COLUMNS.index(name)), {})]
    assert highlighted == ['코멘트']


def test_replace_dialog_all_is_one_undo_and_respects_filter(editor, monkeypatch):
    from tkinter import ttk, messagebox
    import tkinter as tk
    for row in (0, 1):
        editor.table.set_cell_data(row, editor.COLUMNS.index('코멘트'), 'old old')
    editor.modified()
    editor.query.set('가게 2')
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: True)
    editor.find_replace()
    dialog = next(w for w in editor.winfo_children() if isinstance(w, tk.Toplevel))
    box = dialog.winfo_children()[0]
    entries = [w for w in box.winfo_children() if w.winfo_class() == 'TEntry']
    entries[0].insert(0, 'old')
    entries[1].insert(0, 'new')
    buttons = box.winfo_children()[-1].winfo_children()
    next(b for b in buttons if b.cget('text') == '모두 바꾸기').invoke()
    editor.sync()
    assert editor.model.rows[0]['코멘트'] == 'old old'
    assert editor.model.rows[1]['코멘트'] == 'new new'
    next(b for b in buttons if b.cget('text') == '닫기').invoke()
    editor.table.undo()
    editor.sync()
    assert editor.model.rows[1]['코멘트'] == 'old old'
