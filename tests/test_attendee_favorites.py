from copy import deepcopy
import json
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk
import pytest
from src.attendee_favorites import FavoriteStore, split_people, validate_person, validate_people
from src.attendee_picker import AttendeePicker, FavoritesManager, AttendeesEntry
from src import paths, settings
from src.worksheet import write_json

PEOPLE=[{'label':'테스트 A','value':'test.a@example.invalid'}, {'label':'테스트 B','value':'test.b'}]


def test_split_order_and_duplicates():
    assert split_people(' a, B, a, b, , c ') == ['a','B','c']


@pytest.mark.parametrize('value',['', 'x,y','x;y','x\ny','x\u200by','a'*201,'x，y'])
def test_invalid_person(value):
    with pytest.raises(ValueError): validate_person('',value)


def test_labels_optional_and_duplicate_values_blocked():
    assert validate_person('', ' test.a ') == {'label':'test.a','value':'test.a'}
    with pytest.raises(ValueError): validate_people([{'label':'A','value':'a'}, {'label':'B','value':'A'}])


def test_store_roundtrip_and_stale_writer(tmp_path):
    p=tmp_path/'people.json'; first=FavoriteStore(p); second=FavoriteStore(p)
    assert first.people==[] and not p.exists()
    first.save(PEOPLE)
    assert FavoriteStore(p).people==PEOPLE
    with pytest.raises(ValueError,match='다른 창'): second.save([])
    assert FavoriteStore(p).people==PEOPLE


@pytest.mark.parametrize('payload',['broken', json.dumps({'version':2,'people':[]}), json.dumps({'version':1,'people':[{}]})])
def test_corrupt_store_is_not_overwritten(tmp_path,payload):
    p=tmp_path/'people.json';p.write_text(payload)
    with pytest.raises(ValueError): FavoriteStore(p)
    assert p.read_text()==payload


def test_failed_replace_retains_data_and_cleans_lock(tmp_path,monkeypatch):
    import src.attendee_favorites as mod
    p=tmp_path/'people.json';s=FavoriteStore(p);s.save(PEOPLE);before=p.read_bytes()
    def fail(*a): raise PermissionError('test locked')
    monkeypatch.setattr(mod.os,'replace',fail)
    with pytest.raises(PermissionError):s.save([])
    assert p.read_bytes()==before and not list(tmp_path.glob('.attendee-favorites*'))
    assert s.people==PEOPLE


@pytest.fixture
def root(tmp_path,monkeypatch,tk_window):
    monkeypatch.setattr(paths,'base',lambda:tmp_path)
    tk_window.geometry('800x600');tk_window.update()
    return tk_window


@pytest.fixture
def store(tmp_path):
    s=FavoriteStore(tmp_path/'attendee-favorites.json');s.save(PEOPLE);return s


@pytest.fixture
def editor(root,tmp_path):
    from src.worksheet_editor import Editor
    from src.organize import MANIFEST_COLUMNS
    rows=[{**dict.fromkeys(MANIFEST_COLUMNS,''), '거래일':'2026-09-18','금액':'17000',
        '승인번호':f'T{i}','파일명':f'test{i}.pdf','가맹점명':f'테스트 {i}',
        '경비유형':'내부 직원간 식음료','참석자':'base.user','추가 참석자':'manual.user'} for i in range(3)]
    p=tmp_path/'workbook.json';write_json(p,{'version':1,'rows':rows})
    e=Editor(root,p,{**deepcopy(settings.DEFAULTS),'attendee_default':'base.user'})
    root.update();yield e
    if e.winfo_exists():
        if e.pending:e.after_cancel(e.pending);e.pending=None
        e.destroy()


def test_manager_add_save_rename_delete(root,store,monkeypatch):
    m=FavoritesManager(root,store);m.label.set('C');m.value.set('test.c')
    assert m.save() # uncommitted typed item included by Save
    assert len(FavoriteStore(store.path).people)==3
    m=FavoritesManager(root,FavoriteStore(store.path));m.table.selection_set('0');m.select()
    m.label.set('바뀐 이름');assert m.upsert()
    m.table.selection_set('1');m.select()
    monkeypatch.setattr('src.attendee_picker.messagebox.askyesno',lambda *a,**k:True)
    m.remove();assert m.save()
    p=FavoriteStore(store.path).people
    assert [v['value'] for v in p]==[PEOPLE[0]['value'],'test.c'] and p[0]['label']=='바뀐 이름'


def test_manager_cancel_duplicate_and_empty_error(root,store,monkeypatch):
    before=store.path.read_bytes();m=FavoritesManager(root,store)
    assert not m.upsert()
    m.value.set(PEOPLE[0]['value'].upper());assert not m.upsert()
    m.value.set('new.person');assert m.upsert()
    monkeypatch.setattr('src.attendee_picker.messagebox.askyesno',lambda *a,**k:True)
    m.close();assert store.path.read_bytes()==before


def test_picker_filter_preserves_hidden_checks_and_current_values(root,store):
    out=[];p=AttendeePicker(root,' manual.user ',out.append,store)
    p.checked[PEOPLE[0]['value']].set(True)
    p.query.set('테스트 B');p.check_shown(True)
    assert sum(v.get() for v in p.checked.values())==3
    p.check_shown(False)
    assert p.checked[PEOPLE[0]['value']].get() and p.checked['manual.user'].get()
    assert p.apply() and out==['manual.user, test.a@example.invalid']


def test_picker_no_change_keeps_exact_string_and_cancel(root,store):
    out=[];p=AttendeePicker(root,'manual.user,  test.b',out.append,store)
    assert p.apply() and out==['manual.user,  test.b']
    p=AttendeePicker(root,'',out.append,store);p.checked['test.b'].set(True);p.close()
    assert len(out)==1


def test_picker_manual_values_not_saved_as_favorites(root,store):
    before=store.path.read_bytes();out=[];p=AttendeePicker(root,'test.b',out.append,store)
    p.manual.set('test.b, new.person');p.apply()
    assert out==['test.b, new.person'] and store.path.read_bytes()==before


def test_manager_changes_refresh_picker_and_retain_removed_selected(root,store,monkeypatch):
    p=AttendeePicker(root,'test.b',lambda v:None,store);m=p.manage()
    m.table.selection_set('1');m.select()
    monkeypatch.setattr('src.attendee_picker.messagebox.askyesno',lambda *a,**k:True)
    m.remove();m.value.set('new.person');m.label.set('새 사람');m.save()
    assert 'new.person' in p.options and p.checked['test.b'].get()
    assert not p.options['test.b']['registered'] and root.grab_current()==p
    p.close()


def test_table_filtered_cell_apply_atomic_undo_and_no_basic_change(editor,store):
    editor.query.set('테스트 2');editor.table.select_cell(0,editor.COLUMNS.index('추가 참석자'))
    before=deepcopy(editor.model.rows);disk=editor.model.target.read_bytes()
    p=editor.pick_extra_attendees();p.checked['test.b'].set(True);assert p.apply()
    assert editor.model.rows[2]['추가 참석자']=='manual.user, test.b'
    assert editor.model.rows[2]['참석자']=='base.user' and editor.model.rows[:2]==before[:2]
    assert editor.model.target.read_bytes()==disk
    editor.table.undo();assert editor.model.rows==before
    editor.table.redo();assert editor.model.rows[2]['추가 참석자']=='manual.user, test.b'


def test_table_enter_picker_but_f2_and_typing_remain_text(editor,store):
    c=editor.COLUMNS.index('추가 참석자')
    event=SimpleNamespace(column=c,row=0,key='F2',value='manual.user')
    assert editor.begin_cell_edit(event)=='manual.user'
    event.key='x';event.value='x';assert editor.begin_cell_edit(event)=='x'
    event.key='Return';assert editor.begin_cell_edit(event) is None
    editor.update_idletasks()
    picker=next(w for w in editor.winfo_children() if isinstance(w,AttendeePicker))
    picker.close()


def descendants(w):
    return [c for child in w.winfo_children() for c in [child,*descendants(child)]]


def test_row_form_lov_is_draft_until_applied(editor,store,monkeypatch):
    from src.row_editor import RowEditor
    before=deepcopy(editor.model.rows);r=RowEditor(editor,0)
    entry=next(w for w in descendants(r) if isinstance(w,AttendeesEntry))
    p=entry.open();p.checked['test.b'].set(True);p.apply()
    assert r.variables['추가 참석자'].get()=='manual.user, test.b' and editor.model.rows==before
    assert r.apply();assert editor.model.rows[0]['추가 참석자']=='manual.user, test.b'
    editor.table.undo();assert editor.model.rows==before


def test_bulk_picker_selected_rows_only(editor,store):
    editor.table.select_row(1);before=deepcopy(editor.model.rows)
    editor.bulk()
    d=next(w for w in editor.winfo_children() if isinstance(w,tk.Toplevel))
    selector=next(w for w in descendants(d) if isinstance(w,ttk.Combobox) and '추가 참석자' in w.cget('values'))
    selector.set('추가 참석자')
    entry=next(w for w in descendants(d) if isinstance(w,AttendeesEntry))
    p=entry.open();p.checked['test.b'].set(True);p.apply()
    button=next(w for w in descendants(d) if isinstance(w,ttk.Button) and w.cget('text')=='선택 행에 적용')
    button.invoke()
    assert editor.model.rows[1]['추가 참석자']=='test.b'
    assert editor.model.rows[0]==before[0] and editor.model.rows[2]==before[2]
    editor.table.undo();assert editor.model.rows==before


def test_registering_does_not_auto_fill_existing_worksheet(editor,store):
    before=deepcopy(editor.model.rows);m=FavoritesManager(editor,store)
    m.value.set('new.person');m.save()
    assert editor.model.rows==before


def test_picker_rejects_control_character_in_manual(root,store):
    p=AttendeePicker(root,'',lambda v:None,store)
    p.manual.set('bad\nname');assert p.apply() is False and p.winfo_exists();p.close()


def test_empty_check_selection_preserves_concur_blank_semantics(editor,store):
    p=editor.pick_extra_attendees(0);p.check_shown(False);p.apply()
    assert editor.model.rows[0]['추가 참석자']=='' and editor.model.rows[0]['참석자']=='base.user'


def test_home_management_button_disabled_when_busy(monkeypatch,tmp_path,tk_cleanup):
    from src import gui
    monkeypatch.setattr(gui,'_preload',lambda:None)
    monkeypatch.setattr(gui.settings,'load',lambda:deepcopy(settings.DEFAULTS))
    monkeypatch.setattr(paths,'base',lambda:tmp_path)
    app=gui.App();tk_cleanup(app);app.update()
    button=next(w for w in descendants(app) if isinstance(w,ttk.Button) and w.cget('text')=='자주 쓰는 추가 참석자 관리')
    assert button in app.buttons
    button.invoke();manager=next(w for w in app.winfo_children() if isinstance(w,FavoritesManager));manager.close()
    # Registered with tk_cleanup before any assertions.
