from pathlib import Path
from PIL import Image
from src.mileage import MileageBook, new_row
from src import mileage_concur


def prepared(tmp_path):
    image=tmp_path/'map.png'
    Image.new('RGB',(8,8)).save(image)
    book=MileageBook(tmp_path)
    row={**new_row(),'date':'2026-09-18','origin':'출발','destination':'도착',
         'vehicle':'My Car','kind':'long','rate':'470','distance':'10',
         'passengers':'1','description':'업무 이동',
         **book.attach_image(image)}
    book.commit_rows([row]);book.save()
    return book


class Closeable:
    def close(self): pass
    def stop(self): pass


def test_verified_mileage_is_persisted_and_not_created_twice(tmp_path,monkeypatch):
    prepared(tmp_path)
    calls=[]
    monkeypatch.setattr(mileage_concur.ar,'open_report',lambda:(Closeable(),Closeable(),object(),'https://example/reports/R'))
    monkeypatch.setattr(mileage_concur,'create_one',lambda page,url,row,image:(calls.append(row['id']) or 'EXP-1'))
    assert mileage_concur.run(tmp_path,True,None)==0
    saved=MileageBook(tmp_path).rows[0]
    assert saved['concur_state']=='verified' and saved['concur_expense_id']=='EXP-1'
    assert len(calls)==1
    assert mileage_concur.run(tmp_path,True,None)==0
    assert len(calls)==1


def test_uncertain_create_is_never_auto_retried(tmp_path,monkeypatch):
    prepared(tmp_path)
    calls=[]
    monkeypatch.setattr(mileage_concur.ar,'open_report',lambda:(Closeable(),Closeable(),object(),'https://example/reports/R'))
    def uncertain(*args):
        calls.append(1)
        raise mileage_concur.UncertainMileageCreate('save unknown')
    monkeypatch.setattr(mileage_concur,'create_one',uncertain)
    assert mileage_concur.run(tmp_path,True,None)==1
    saved=MileageBook(tmp_path).rows[0]
    assert saved['concur_state']=='needs_review'
    assert len(calls)==1
    assert mileage_concur.run(tmp_path,True,None)==0
    assert len(calls)==1


def test_preview_does_not_open_browser(tmp_path,monkeypatch):
    prepared(tmp_path)
    monkeypatch.setattr(mileage_concur.ar,'open_report',lambda:(_ for _ in ()).throw(AssertionError('browser opened')))
    assert mileage_concur.run(tmp_path,False,None)==0


def test_expense_id_parser():
    assert mileage_concur._expense_id('https://x/nui/expense/reports/R/expenses/ABC123?x=1')=='ABC123'
    assert mileage_concur._expense_id('https://x/nui/expense/reports/R') is None
