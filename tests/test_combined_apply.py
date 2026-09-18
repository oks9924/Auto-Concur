from types import SimpleNamespace

from src import update_concur, sequential_workflow, mileage_concur, settings
from src.concur_workflow import RunResult


class Closeable:
    def __init__(self):
        self.closed=0
    def close(self):
        self.closed+=1
    def stop(self):
        self.closed+=1


def report():
    return SimpleNamespace(
        title='Synthetic report',
        key=('eu2.concursolutions.com','R'),
        url='https://eu2.concursolutions.com/nui/expense/reports/R',
        rows=(SimpleNamespace(expense_id='E1'),),
    )


def test_card_and_mileage_share_one_browser_session(tmp_path,monkeypatch):
    pw,ctx,page=Closeable(),Closeable(),object()
    rep=report()
    calls=[]
    monkeypatch.setattr(update_concur,'precheck',lambda *a,**k:None)
    monkeypatch.setattr(update_concur,'open_report',lambda automatic=True:(pw,ctx,page,rep))
    monkeypatch.setattr(update_concur.console,'confirm_action',lambda *a,**k:True)
    monkeypatch.setattr(mileage_concur,'status',
        lambda *a,**k:{'total':2,'pending_ids':['M1','M2'],'pending':2,'verified':0,'needs_review':0})
    def card(*args,**kwargs):
        calls.append(('card',args[0],args[1].url,kwargs.get('confirm')))
        return RunResult(0,'card ok')
    def mileage(*args,**kwargs):
        calls.append(('mileage',args[0],args[1]))
        return RunResult(0,'mileage ok')
    monkeypatch.setattr(sequential_workflow,'run',card)
    monkeypatch.setattr(mileage_concur,'run_in_session',mileage)

    result=update_concur.run(tmp_path,True,1,None,None,cfg=dict(settings.DEFAULTS))
    assert result==0
    assert calls==[
        ('card',page,rep.url,False),
        ('mileage',page,rep.url),
    ]
    assert 'card ok' in result.summary and 'mileage ok' in result.summary
    assert ctx.closed==1 and pw.closed==1


def test_combined_confirmation_cancel_writes_neither_path(tmp_path,monkeypatch):
    pw,ctx,page=Closeable(),Closeable(),object()
    rep=report()
    called=[]
    monkeypatch.setattr(update_concur,'precheck',lambda *a,**k:None)
    monkeypatch.setattr(update_concur,'open_report',lambda automatic=True:(pw,ctx,page,rep))
    monkeypatch.setattr(update_concur.console,'confirm_action',lambda *a,**k:False)
    monkeypatch.setattr(mileage_concur,'status',
        lambda *a,**k:{'total':1,'pending_ids':['M1'],'pending':1,'verified':0,'needs_review':0})
    monkeypatch.setattr(sequential_workflow,'run',lambda *a,**k:called.append('card'))
    monkeypatch.setattr(mileage_concur,'run_in_session',lambda *a,**k:called.append('mileage'))

    result=update_concur.run(tmp_path,True,1,None,None,cfg=dict(settings.DEFAULTS))
    assert result==0 and not called
    assert '취소' in result.summary
    assert ctx.closed==1 and pw.closed==1
