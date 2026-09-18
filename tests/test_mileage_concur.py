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
    assert mileage_concur.run(tmp_path,True,None)==1
    assert len(calls)==1


def test_preview_does_not_open_browser(tmp_path,monkeypatch):
    prepared(tmp_path)
    monkeypatch.setattr(mileage_concur.ar,'open_report',lambda:(_ for _ in ()).throw(AssertionError('browser opened')))
    assert mileage_concur.run(tmp_path,False,None)==0


def test_expense_id_parser():
    assert mileage_concur._expense_id('https://x/nui/expense/reports/R/expenses/ABC123?x=1')=='ABC123'
    assert mileage_concur._expense_id('https://x/nui/expense/reports/R') is None


class FakePage:
    def __init__(self):
        self.url='https://eu2.concursolutions.com/nui/expense/reports/R'
        self.gotos=[]
    def goto(self,url,wait_until=None):
        self.url=url
        self.gotos.append(url)
    def wait_for_timeout(self,ms):
        pass


def test_create_one_clicks_manual_create_before_mileage_type(tmp_path,monkeypatch):
    page=FakePage()
    events=[]
    ids=iter([{'OLD'},{'OLD','NEW'}])
    monkeypatch.setattr(mileage_concur,'check_context',lambda *a:None)
    monkeypatch.setattr(mileage_concur,'_report_ids',lambda *a:next(ids))
    monkeypatch.setattr(mileage_concur.ui,'click_target',
        lambda page,script,what,*args,**kwargs:events.append(what))
    def manual(page,labels,what,timeout=10000):
        assert '수동으로 경비 생성' in labels
        events.append(what)
    monkeypatch.setattr(mileage_concur,'_click_unique_text',manual)
    def mileage_type(page):
        events.append('자동차 마일리지 유형')
        page.url='https://eu2.concursolutions.com/nui/expense/reports/R/expenses/NEW'
    monkeypatch.setattr(mileage_concur,'_select_mileage_type',mileage_type)
    monkeypatch.setattr(mileage_concur,'_fill',lambda *a,**k:None)
    monkeypatch.setattr(mileage_concur,'_select_vehicle',lambda *a,**k:None)
    monkeypatch.setattr(mileage_concur,'_upload_map',lambda *a,**k:None)
    row={'date':'2026-09-18','origin':'A','destination':'B','vehicle':'V',
         'distance':'1','passengers':'1','description':'D'}
    assert mileage_concur.create_one(page,page.url,row,tmp_path/'map.png')=='NEW'
    assert events[:3]==['경비 추가','수동으로 경비 생성','자동차 마일리지 유형']
    assert '마일리지 저장' in events


def test_multiple_mileage_rows_each_persist_verified_id(tmp_path,monkeypatch):
    book=prepared(tmp_path)
    image=tmp_path/'map2.png';Image.new('RGB',(8,8)).save(image)
    second={**new_row(),'date':'2026-09-19','origin':'C','destination':'D',
            'vehicle':'My Car','kind':'long','rate':'470','distance':'2',
            'passengers':'1','description':'두 번째',**book.attach_image(image)}
    book.commit_rows([*book.rows,second]);book.save()
    calls=[]
    monkeypatch.setattr(mileage_concur.ar,'open_report',
        lambda:(Closeable(),Closeable(),object(),'https://example/reports/R'))
    monkeypatch.setattr(mileage_concur,'create_one',
        lambda page,url,row,image:(calls.append(row['id']) or 'EXP-'+row['id'][:6]))
    assert mileage_concur.run(tmp_path,True,None)==0
    saved=MileageBook(tmp_path).rows
    assert len(calls)==2
    assert all(r['concur_state']=='verified' and r['concur_expense_id'].startswith('EXP-') for r in saved)
    assert mileage_concur.run(tmp_path,True,None)==0
    assert len(calls)==2
