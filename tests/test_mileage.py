from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import tkinter as tk
import pytest
from PIL import Image
from src import paths, settings
from src.mileage import MileageBook, LocalRows, checked_row, new_row, number, validate_vehicles
from src.worksheet import write_json


def image(tmp_path):
    path=tmp_path/'map.png';Image.new('RGB',(20,20)).save(path);return path


def ready(book,tmp_path):
    return {**new_row(),'date':'2026-09-18','origin':'테스트 출발','destination':'테스트 도착','vehicle':'My Car',
            'kind':'long','rate':'470','distance':'12.5','passengers':'1','description':'업무 방문',
            **book.attach_image(image(tmp_path))}


def test_estimate_and_vehicle_no_inference(tmp_path):
    book=MileageBook(tmp_path);row=ready(book,tmp_path)
    assert checked_row(row)['estimate']=='3500.0'
    row['passengers']='4'
    assert checked_row(row)['estimate']=='3500.0'
    assert validate_vehicles([{'vehicle':'I choose my own name','kind':'short'}])[0]['rate']=='280'


@pytest.mark.parametrize('field,value',[('origin',''),('destination',''),('distance','0'),('distance','-1'),
    ('distance','1,200'),('distance','NaN'),('distance','Infinity'),('distance','1e4'),
    ('passengers','1.5'),('passengers','-1'),('date','bad'),('date','2026-02-30'),('description',''),
    ('kind','auto'),('rate','999'),('map','')])
def test_invalid_row(field,value,tmp_path):
    book=MileageBook(tmp_path);row=ready(book,tmp_path);row[field]=value
    with pytest.raises(ValueError): checked_row(row)


def test_independent_storage_restore_undo(tmp_path):
    card=tmp_path/'workbook.json';card.write_text('original card data')
    book=MileageBook(tmp_path);row=ready(book,tmp_path)
    book.commit_rows([row]);assert book.dirty
    book.undo();assert not book.rows
    book.redo();book.save()
    assert MileageBook(tmp_path).rows[0]['vehicle']=='My Car'
    assert card.read_text()=='original card data'
    original=image(tmp_path);original.unlink()
    assert book.check_image(book.rows[0]).is_file()


def test_missing_map_and_tampering(tmp_path):
    book=MileageBook(tmp_path);row=ready(book,tmp_path);book.commit_rows([row]);book.save()
    book.check_image(row).write_bytes(b'changed')
    with pytest.raises(ValueError): book.save()
    row['map']='../private.png'
    with pytest.raises(ValueError): book.image_path(row)


def test_invalid_image_and_duplicate_id(tmp_path):
    book=MileageBook(tmp_path)
    bad=tmp_path/'bad.png';bad.write_text('not an image')
    with pytest.raises(OSError): book.attach_image(bad)
    row=ready(book,tmp_path)
    with pytest.raises(ValueError): book.commit_rows([row,row])


def test_stale_store_and_duplicate_vehicle(tmp_path):
    path=tmp_path/'v.json';a=LocalRows(path,validate_vehicles);b=LocalRows(path,validate_vehicles)
    a.rows=[{'vehicle':'V','kind':'short'}];a.save()
    b.rows=[{'vehicle':'other','kind':'long'}]
    with pytest.raises(ValueError): b.save()
    with pytest.raises(ValueError): validate_vehicles([{'vehicle':'V','kind':'long'},{'vehicle':'v','kind':'short'}])
    assert LocalRows(path,validate_vehicles).rows[0]['vehicle']=='V'


def test_failed_save_preserves_original(tmp_path,monkeypatch):
    book=MileageBook(tmp_path);book.commit_rows([ready(book,tmp_path)]);book.save();original=book.path.read_bytes()
    book.rows[0]['description']='changed'
    monkeypatch.setattr('src.mileage.os.replace',lambda *a:(_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError): book.save()
    assert book.path.read_bytes()==original and book.dirty


@pytest.fixture
def editor(tmp_path,monkeypatch,tk_window):
    from src.worksheet_editor import Editor
    from src.organize import MANIFEST_COLUMNS
    monkeypatch.setattr(paths,'base',lambda:tmp_path)
    root=tk_window;root.geometry('1200x900');root.update()
    row={**dict.fromkeys(MANIFEST_COLUMNS,''),'승인번호':'T','파일명':'T.pdf','거래일':'2026-09-18','금액':'100', '가맹점명':'테스트'}
    file=tmp_path/'workbook.json';write_json(file,{'version':1,'rows':[row]})
    e=Editor(root,file,deepcopy(settings.DEFAULTS));root.update()
    yield e
    # tk_window owns cleanup, including when editor construction fails.


def fill(form,folder):
    from src.mileage import vehicles_store
    store=vehicles_store();store.rows=[{'vehicle':'my-name','kind':'long'}];store.save()
    form.reload_vehicles()
    for key,value in {'origin':'출발','destination':'도착','vehicle':'my-name','distance':'10.5','passengers':'2'}.items(): form.vars[key].set(value)
    form.description.insert('1.0','출장 경로')
    form.chosen_image=str(image(folder))


def test_add_panel_form_separate_rows_and_image(editor):
    original=deepcopy(editor.model.rows);original_disk=editor.model.target.read_bytes()
    assert editor.mileage_panel is None
    form=editor.add_mileage();fill(form,editor.model.target.parent)
    assert form.apply()
    panel=editor.mileage_panel
    assert len(panel.book.rows)==1 and len(editor.tables.panes())==2
    assert panel.book.rows[0]['estimate']=='2940.0'
    assert panel.save() and editor.model.rows==original
    assert editor.model.target.read_bytes()==original_disk
    panel.undo();assert not panel.book.rows
    panel.redo();assert len(panel.book.rows)==1


def test_cancel_does_not_create_row(editor):
    form=editor.add_mileage();fill(form,editor.model.target.parent);form.close()
    assert not editor.mileage_panel.book.rows


def test_saved_panel_reopens(editor):
    from src.worksheet_editor import Editor
    panel=editor.show_mileage();panel.book.commit_rows([ready(panel.book,editor.model.target.parent)]);panel.save()
    second=Editor(editor.master,editor.model.target,deepcopy(settings.DEFAULTS))
    assert second.mileage_panel is not None and len(second.mileage_panel.book.rows)==1
    second.destroy()


def test_vehicle_manager_save_and_cancel(editor,monkeypatch):
    from src.mileage_ui import VehicleManager
    from src.mileage import vehicles_store
    dialog=VehicleManager(editor);dialog.vehicle.set('Unique name');dialog.kind.set('short');assert dialog.save()
    assert vehicles_store().rows==[{'vehicle':'Unique name','kind':'short','rate':'280'}]
    dialog=VehicleManager(editor);dialog.vehicle.set('Discarded')
    monkeypatch.setattr('src.mileage_ui.messagebox.askyesno',lambda *a,**k:True)
    dialog.close();assert len(vehicles_store().rows)==1


def test_copy_mileage_changes_date_only_and_clears_concur_state(editor):
    panel=editor.show_mileage()
    source=ready(panel.book,editor.model.target.parent)
    source.update({
        'concur_state':'verified',
        'concur_stage':'verified',
        'concur_expense_id':'EXP-OLD',
        'concur_receipt_verified':True,
        'concur_note':'old result',
    })
    panel.book.commit_rows([source]);panel.render()
    panel.table.selection_set(source['id'])

    form=panel.copy()
    assert form is not None and form.copying
    assert form.original['id']!=source['id']
    assert not [key for key in form.original if key.startswith('concur_')]
    for key in ('origin','destination','vehicle','kind','rate','distance','passengers',
                'description','map','map_sha256','map_name'):
        assert form.original[key]==source[key]

    form.vars['date'].set('2026-09-19')
    assert form.apply()
    assert len(panel.book.rows)==2
    copied=panel.book.rows[1]
    assert copied['date']=='2026-09-19'
    assert copied['id']!=source['id']
    assert not [key for key in copied if key.startswith('concur_')]
    for key in ('origin','destination','vehicle','kind','rate','distance','passengers',
                'description','map','map_sha256','map_name'):
        assert copied[key]==source[key]


def test_copy_mileage_cancel_creates_nothing(editor):
    panel=editor.show_mileage()
    source=ready(panel.book,editor.model.target.parent)
    panel.book.commit_rows([source]);panel.render()
    panel.table.selection_set(source['id'])
    form=panel.copy()
    form.close()
    assert len(panel.book.rows)==1 and panel.book.rows[0]['id']==source['id']



def test_corrected_long_short_rates_and_old_rows_migrate(tmp_path):
    assert validate_vehicles([{'vehicle':'long-car','kind':'long'}])[0]['rate']=='280'
    assert validate_vehicles([{'vehicle':'short-car','kind':'short'}])[0]['rate']=='470'
    book=MileageBook(tmp_path)
    row=ready(book,tmp_path)
    row['kind']='long';row['rate']='470'
    migrated=checked_row(row)
    assert migrated['rate']=='280'
    assert migrated['estimate']=='3500.0'
