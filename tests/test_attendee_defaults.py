from copy import deepcopy
import tkinter as tk
import pytest
from src.attendee_defaults import attendee_on_type_change
from src.expense_policy import ATTENDEE_REQUIRED_TYPE as MEAL
from src import settings

DEFAULT = 'test.user'


@pytest.mark.parametrize('old,new,before,after,default,expected', [
    ('', MEAL, '', '', DEFAULT, DEFAULT),
    ('주차비', MEAL, '', '  ', DEFAULT, DEFAULT),
    ('', MEAL, '', 'manual.user', DEFAULT, None),
    ('', MEAL, 'existing.user', '', DEFAULT, None),
    (MEAL, MEAL, '', '', DEFAULT, None),
    (MEAL, '주차비', '', '', DEFAULT, None),
    ('', '외부인 포함 식음료', '', '', DEFAULT, None),
    ('', MEAL, '', '', ' ', None),
    ('', MEAL, '', '', 'test.a, test.b', 'test.a, test.b'),
])
def test_rule(old,new,before,after,default,expected):
    assert attendee_on_type_change({'경비유형':old,'참석자':before},
        {'경비유형':new,'참석자':after}, {'attendee_default':default}) == expected


@pytest.fixture
def editor(tmp_path, tk_window):
    from src.worksheet_editor import Editor
    from src.worksheet import write_json
    from src.organize import MANIFEST_COLUMNS
    root=tk_window; root.geometry('900x700'); root.update()
    rows=[{**dict.fromkeys(MANIFEST_COLUMNS,''), '거래일':'2026-09-17', '금액':'17000',
           '승인번호':f'T{i}', '파일명':f'test{i}.pdf', '가맹점명':f'상점{i}',
           '경비유형':'주차비', '추가 참석자':'extra.user'} for i in range(3)]
    path=tmp_path/'workbook.json'; write_json(path,{'version':1,'rows':rows})
    cfg={**deepcopy(settings.DEFAULTS),'attendee_default':DEFAULT}
    e=Editor(root,path,cfg); root.update()
    yield e
    # tk_window owns cleanup, including when editor construction fails.


def col(editor,name): return editor.COLUMNS.index(name)


def set_cell(editor,row,name,value):
    editor.table.set_data(row,col(editor,name),data=value,undo=True,emit_event=True)


def test_native_edit_and_atomic_undo_redo(editor):
    original=deepcopy(editor.model.rows); disk=editor.model.target.read_bytes()
    k=col(editor,'경비유형')
    editor.table.MT.set_cell_data_undo(r=0,c=k,value=MEAL)
    assert editor.model.rows[0]['참석자']==DEFAULT
    assert editor.model.rows[0]['추가 참석자']=='extra.user'
    assert editor.model.target.read_bytes()==disk
    editor.table.undo(); assert editor.model.rows==original
    editor.table.redo(); assert editor.model.rows[0]['참석자']==DEFAULT


def test_existing_attendee_kept(editor):
    set_cell(editor,0,'참석자','existing.user')
    set_cell(editor,0,'경비유형',MEAL)
    assert editor.model.rows[0]['참석자']=='existing.user'


def test_clear_unrelated_edit_save_and_reopen_never_refill(editor):
    set_cell(editor,0,'경비유형',MEAL)
    set_cell(editor,0,'참석자','')
    set_cell(editor,0,'코멘트','description')
    assert editor.save()
    editor.reload()
    assert editor.model.rows[0]['참석자']==''
    assert editor.model.rows[0]['경비유형']==MEAL


def test_undo_redo_does_not_inject_current_default(editor):
    set_cell(editor,0,'경비유형',MEAL)
    set_cell(editor,0,'참석자','')
    set_cell(editor,0,'경비유형','주차비')
    editor.cfg['attendee_default']='different.user'
    editor.table.undo()
    assert editor.model.rows[0]['경비유형']==MEAL and editor.model.rows[0]['참석자']==''
    editor.table.redo()
    assert editor.model.rows[0]['참석자']==''


def test_bulk_table_change_preserves_existing_names(editor):
    set_cell(editor,1,'참석자','existing.user')
    before=deepcopy(editor.model.rows)
    data=deepcopy(editor.table.get_sheet_data())
    for r in (0,1): data[r][col(editor,'경비유형')]=MEAL
    editor.table.set_data(0,0,data=data,undo=True,emit_event=True)
    assert [r['참석자'] for r in editor.model.rows]==[DEFAULT,'existing.user','']
    editor.table.undo(); assert editor.model.rows==before


def test_filtered_paste_uses_data_row_indexes(editor):
    editor.query.set('상점2'); editor.table.select_cell(0,col(editor,'경비유형'))
    editor.clipboard_clear(); editor.clipboard_append(MEAL); editor.update()
    editor.table.paste()
    assert [r['참석자'] for r in editor.model.rows]==['','',DEFAULT]
    editor.table.undo(); assert editor.model.rows[2]['참석자']==''
    assert editor.model.rows[2]['경비유형']=='주차비'


def test_paste_provided_attendee_wins(editor):
    editor.table.select_cell(0,col(editor,'경비유형'))
    editor.clipboard_clear(); editor.clipboard_append(MEAL+'\t\t\tmanual.user');editor.update()
    editor.table.paste()
    assert editor.model.rows[0]['참석자']=='manual.user'


def test_empty_default_and_other_types_unchanged(editor):
    editor.cfg['attendee_default']=''
    set_cell(editor,0,'경비유형',MEAL)
    assert editor.model.rows[0]['참석자']==''
    editor.cfg['attendee_default']=DEFAULT
    set_cell(editor,1,'경비유형','숙박비')
    assert editor.model.rows[1]['참석자']==''


def test_form_fills_on_selection_and_cancel_preserves_table(editor,monkeypatch):
    from src.row_editor import RowEditor
    original=deepcopy(editor.model.rows)
    dialog=RowEditor(editor,0)
    dialog.variables['경비유형'].set(MEAL)
    assert dialog.variables['참석자'].get()==DEFAULT
    assert editor.model.rows==original
    monkeypatch.setattr('src.row_editor.messagebox.askyesno',lambda *a,**k:True)
    dialog.cancel(); assert editor.model.rows==original


def test_form_clear_after_selection_stays_empty_on_apply(editor):
    from src.row_editor import RowEditor
    original=deepcopy(editor.model.rows)
    dialog=RowEditor(editor,0)
    dialog.variables['경비유형'].set(MEAL)
    assert dialog.variables['참석자'].get()==DEFAULT
    dialog.variables['참석자'].set('')
    assert dialog.apply()
    assert editor.model.rows[0]['참석자']==''
    editor.table.undo(); assert editor.model.rows==original
    editor.table.redo(); assert editor.model.rows[0]['참석자']==''


def test_form_apply_and_existing_meal_reopen(editor):
    from src.row_editor import RowEditor
    dialog=RowEditor(editor,1); dialog.variables['경비유형'].set(MEAL)
    assert dialog.apply() and editor.model.rows[1]['참석자']==DEFAULT
    set_cell(editor,1,'참석자','')
    dialog=RowEditor(editor,1)
    assert dialog.variables['참석자'].get()==''
    dialog.finish()


def test_code_alias_is_internal_but_not_external():
    cfg={'expense_type_codes':{'Internal Meal':'01182','External Meal':'01093'},'attendee_default':DEFAULT}
    assert attendee_on_type_change({}, {'경비유형':'Internal Meal'},cfg)==DEFAULT
    assert attendee_on_type_change({}, {'경비유형':'External Meal'},cfg) is None
