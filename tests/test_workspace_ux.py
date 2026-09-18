from copy import deepcopy
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import pytest

from src.result_viewer import load_records, ResultViewer
from src.worksheet import write_json
from src import settings


def payload(state='verified', message=''):
    return {'version': 1, 'tasks': {
        'https://example.invalid/nui/expense/reports/REPORT|EXPENSE|edit|hash':
        {'state': state, 'label': '테스트 경비', 'message': message, 'updated': '2026-09-17T09:00:00+00:00'}}}


def test_missing_history_does_not_create_file(tmp_path):
    path = tmp_path/'concur-progress.json'
    assert load_records(path) == [] and not path.exists()


@pytest.mark.parametrize('bad', [[], {}, {'version': 2, 'tasks': {}}, {'version': 1, 'tasks': []},
                              {'version': 1, 'tasks': {'broken': {}}}])
def test_bad_history_reported_without_rewriting(tmp_path, bad):
    path = tmp_path/'concur-progress.json'; path.write_text(json.dumps(bad))
    original = path.read_bytes()
    with pytest.raises(ValueError): load_records(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize('state,expected', [('verified','당시 검증됨'),('needs_review','확인 필요'),
    ('running','중단 여부 확인'),('pending','실행 전 확인'),('surprise','상태 미확인')])
def test_history_state_is_not_live_success(tmp_path, state, expected):
    path=tmp_path/'concur-progress.json'; write_json(path,payload(state))
    before=path.read_bytes(); rows=load_records(path)
    assert rows[0].state == expected and rows[0].report == 'REPORT'
    assert rows[0].expense == 'EXPENSE' and rows[0].kind == '경비 입력'
    assert before == path.read_bytes()


@pytest.fixture
def root():
    app=tk.Tk(); app.geometry('800x600'); app.update()
    yield app
    if app.winfo_exists():
        for task in app.tk.call('after','info'): app.after_cancel(task)
        app.destroy()


@pytest.fixture
def editor(root, tmp_path):
    from src.worksheet_editor import Editor
    from src import organize
    rows=[{**dict.fromkeys(organize.MANIFEST_COLUMNS, ''), '거래일':'2026-09-17', '금액':'17000',
           '승인번호':f'T{i}', '파일명':f'test{i}.pdf', '가맹점명':f'테스트 {i}', '경비유형':'숙박비'} for i in range(2)]
    path=tmp_path/'workbook.json'; write_json(path,{'version':1,'rows':rows})
    e=Editor(root,path,deepcopy(settings.DEFAULTS),on_run=lambda:None);root.update()
    yield e
    if e.winfo_exists():
        if e.pending:e.after_cancel(e.pending)
        e.destroy()


def test_history_search_and_review_filter(root,tmp_path):
    path=tmp_path/'concur-progress.json';data=payload()
    data['tasks']['https://example.invalid/reports/REPORT|E2|receipt|h']={
        'state':'pending','label':'영수증 test','updated':'2026-09-17T10:00:00+00:00','message':'날짜 확인'}
    write_json(path,data)
    view=ResultViewer(root,lambda:tmp_path);view.pack();view.reload()
    assert len(view.table.get_children())==2
    view.filter.set('확인 필요');assert len(view.visible)==1 and view.visible[0].expense=='E2'
    view.query.set('날짜');assert len(view.visible)==1
    view.query.set('없는이름');assert not view.visible
    view.destroy()


def test_history_error_clears_stale_success(root,tmp_path):
    path=tmp_path/'concur-progress.json';write_json(path,payload())
    view=ResultViewer(root,lambda:tmp_path);view.reload();assert view.records
    path.write_text('broken');view.reload()
    assert not view.records and not view.table.get_children()
    assert '읽기 실패' in view.summary.cget('text')
    view.destroy()


def test_filtered_detail_edits_correct_row_and_undo(editor,root):
    editor.query.set('테스트 1');editor.table.select_cell(0,4)
    original=deepcopy(editor.model.rows); before=editor.model.target.read_bytes()
    dialog=editor.edit_row();assert dialog.row_index==1
    dialog.texts['코멘트'].insert('1.0','첫 줄\n두 번째 줄')
    dialog.variables['입실날짜'].set('2026-09-17');dialog.variables['퇴실날짜'].set('2026-09-19')
    assert dialog.apply() is True
    editor.sync()
    assert editor.model.rows[0]==original[0]
    assert editor.model.rows[1]['코멘트']=='첫 줄\n두 번째 줄'
    assert editor.model.rows[1]['입실날짜']=='2026-09-17'
    for key in ('거래일','금액','가맹점명','승인번호','파일명'):
        assert editor.model.rows[1][key]==original[1][key]
    assert editor.model.target.read_bytes()==before  # table edit, not persisted yet
    editor.table.undo();editor.sync();assert editor.model.rows==original


def test_detail_cancel_preserves_edits_and_grab(editor,root,monkeypatch):
    from src import row_editor
    editor.table.select_cell(0,4);before=deepcopy(editor.model.rows)
    dialog=editor.edit_row();dialog.variables['참석자'].set('test-person')
    monkeypatch.setattr(row_editor.messagebox,'askyesno',lambda *a,**k:False)
    dialog.cancel();assert dialog.winfo_exists()
    monkeypatch.setattr(row_editor.messagebox,'askyesno',lambda *a,**k:True)
    dialog.cancel();assert editor.model.rows==before and root.grab_current()==editor


@pytest.mark.parametrize('start,end', [('2026-09-20','2026-09-19'),('2026-09-17',''),('bad','2026-09-19')])
def test_detail_invalid_dates_do_not_apply(editor,start,end):
    editor.table.select_cell(0,4);before=deepcopy(editor.model.rows)
    dialog=editor.edit_row();dialog.variables['입실날짜'].set(start);dialog.variables['퇴실날짜'].set(end)
    assert dialog.apply() is False and editor.model.rows==before
    assert dialog.error.cget('text');dialog.finish()


def test_unknown_type_not_given_guessed_green_guide(editor):
    editor.table.select_cell(0,4);dialog=editor.edit_row()
    dialog.variables['경비유형'].set('조사되지 않은 테스트 유형')
    assert '미등록' in dialog.guide.cget('text')
    assert all('입력 안내' not in label.cget('text') for label in dialog.labels.values())
    dialog.finish()


def test_cancel_run_scope_does_not_save_or_call(editor,monkeypatch):
    from src import worksheet_editor as we
    calls=[];editor.on_run=lambda:calls.append(True)
    before=editor.model.target.read_bytes()
    monkeypatch.setattr(we.messagebox,'askokcancel',lambda *a,**k:False)
    editor.run();assert editor.winfo_exists() and calls==[]
    assert editor.model.target.read_bytes()==before


def test_scope_confirmation_mentions_all_rows(editor,monkeypatch):
    from src import worksheet_editor as we
    texts=[];editor.query.set('테스트 1')
    def cancel(title,message,**kwargs):texts.append(message);return False
    monkeypatch.setattr(we.messagebox,'askokcancel',cancel)
    editor.run();assert '표시 1건 / 작업지 전체 2건' in texts[0]
    assert '선택 행에 관계없이' in texts[0]


def test_no_selection_detail_does_not_guess_row(editor,monkeypatch):
    from src import worksheet_editor as we
    editor.table.deselect('all'); notices=[]
    monkeypatch.setattr(we.messagebox,'showinfo',lambda *a,**k:notices.append(a))
    assert editor.edit_row() is None and notices


@pytest.fixture
def app(monkeypatch,tmp_path):
    from src import gui
    monkeypatch.setattr(gui,'_preload',lambda:None)
    monkeypatch.setattr(gui.settings,'load',lambda:{**deepcopy(settings.DEFAULTS),'downloads_dir':str(tmp_path)})
    a=gui.App();a.update()
    yield a
    for task in a.tk.call('after','info'):a.after_cancel(task)
    a.destroy()


def test_workspace_keeps_dates_and_settings(app):
    from src.calendar_input import DateEntry
    def walk(w):
        yield w
        for child in w.winfo_children():yield from walk(child)
    assert len([w for w in walk(app) if isinstance(w,DateEntry)])==2
    assert len(app.tabs.tabs())==3
    assert len(app.buttons)>=10
    assert app.from_date.get() and app.to_date.get()


def test_activity_disables_inputs_without_fake_percentage(app):
    from src.workspace_ui import set_busy
    set_busy(app,True,'진행 중');app.update()
    assert all(w.instate(['disabled']) for w in app.inputs)
    set_busy(app,False,'사용자가 반영을 취소했습니다.')
    assert app.run_state.get()=='사용자가 반영을 취소했습니다.'
    assert all(not w.instate(['disabled']) for w in app.inputs)


@pytest.mark.parametrize('text', ['0','-1','oops','1.5','１２'])
def test_bad_limit_never_runs_unlimited(app,text,monkeypatch):
    from src import gui
    calls=[];notices=[];app.limit.set(text)
    monkeypatch.setattr(gui.messagebox,'showerror',lambda *a,**k:notices.append(a))
    app._start('test',lambda:calls.append(True))
    assert notices and not calls and not app.busy


def test_end_uses_exact_partial_or_cancel_message(app):
    app.busy=True
    app.events.put(('end','매칭하지 못한 거래 2건은 처리하지 않았습니다.'))
    app._drain()
    assert not app.busy and '2건' in app.run_state.get()
    assert '모두 완료' not in app.run_state.get()


def test_close_blocked_during_work(app,monkeypatch):
    from src import gui
    app.busy=True; notices=[]
    monkeypatch.setattr(gui.messagebox,'showinfo',lambda *a,**k:notices.append(a))
    app.close_window();assert app.winfo_exists() and notices
    app.busy=False


def test_small_workspace_scrolls_to_lower_tools(app):
    app.geometry('760x530');app.update()
    area=app.home_scroll
    assert area.body.winfo_height() > area.canvas.winfo_height()
    area.canvas.yview_moveto(1);app.update()
    assert area.canvas.yview()[0] > 0


def test_history_does_not_contact_concur(app,tmp_path):
    app.show_results()
    assert not (tmp_path/'concur-progress.json').exists()
    assert app.tabs.select()==str(app.result_tab)
