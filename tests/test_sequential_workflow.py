from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src import sequential_workflow as sw, attach_receipts as ar, sheet, settings
from src.concur_detail import decode_detail, same_identity, ContextChanged, DetailDriver
from src.concur_formats import using_formats
from src.execution import Journal, Task, task_key
from src.report_session import Report, report_key

URL = 'https://eu2.concursolutions.com/nui/expense/reports/TEST'
DAY = date(2026, 9, 18)


def source(tmp_path, day=DAY, amount=17000, merchant='Test shop', approval='A'):
    p = tmp_path / (approval + '.pdf'); p.write_bytes(b'synthetic receipt')
    return ar.Slip(p, day, amount, merchant, approval)


def row(i=0, day=DAY, amount=17000, merchant='Test shop', receipt=False):
    return ar.Row(i, day, amount, '', f'E{i}', '주차비', merchant, receipt,
                  str(day) if day else '', str(amount) if amount else '')


def entry(s, comment='Test comment'):
    return sheet.SheetRow(s.when, s.amount, s.merchant, s.approval, '주차비', '', comment, '')


def raw(**kw):
    return dict(date='2026-09-18', amount='17,000.00', vendor='Test shop', currency='KRW',
                expenseType='주차비', dateField=True, amountField=True, vendorField=True, **kw)


@pytest.mark.parametrize('day,fmt', [('2026-09-18','YMD'), ('09/18/2026','MDY'), ('18/09/2026','DMY')])
def test_detail_date_formats(day, fmt):
    value=raw(); value['date']=day
    with using_formats({'concur_date_order':fmt}):
        parsed = decode_detail(value, row(day=None, amount=None))
        assert parsed.when == DAY and parsed.amount == 17000


@pytest.mark.parametrize('currency', ['USD', 'EUR', 'USD Dollar', '$', '', 'unknown'])
def test_no_cross_currency_matching(currency):
    value=raw(); value['currency']=currency
    with pytest.raises(ar.AttachError, match='통화'):
        decode_detail(value, row())


def test_detail_placeholder_resolves_ambiguous_day():
    value=raw(placeholder='MM/DD/YYYY'); value['date']='09/08/2026'
    assert decode_detail(value, row()).when == date(2026,9,8)


@pytest.mark.parametrize('key,value', [('date',''),('amount','oops'),('vendorField',False)])
def test_unknown_detail_not_replaced_from_list(key,value):
    data=raw(); data[key]=value
    with pytest.raises(ar.AttachError): decode_detail(data,row())


def test_identity_ignores_type_and_receipt_but_not_vendor_date_amount():
    a=row(); b=deepcopy(a); b.expense_type='Another'; b.has_receipt=True
    assert same_identity(a,b)
    for attr,value in [('vendor','Elsewhere'),('when',DAY+timedelta(days=1)),('amount',999),('expense_id','E9')]:
        c=deepcopy(b); setattr(c,attr,value); assert not same_identity(a,c)


def test_sources_combine_once_and_duplicate_local_rows_stay_ambiguous(tmp_path):
    s=source(tmp_path); e=entry(s)
    assert len(sw.targets_from([s],[e]))==1
    targets=sw.targets_from([s],[e,e])
    assert len(targets)==3
    assert sw.choose_target(row(),targets,1)[0] is None


def test_exact_date_before_day_tolerance(tmp_path):
    a=source(tmp_path,day=DAY-timedelta(days=1),approval='A')
    b=source(tmp_path,approval='B')
    targets=sw.targets_from([a,b],[])
    assert sw.choose_target(row(),targets,1)[0]==1


def test_actual_duplicates_disambiguate_vendor(tmp_path):
    a=source(tmp_path,merchant='alpha store',approval='A')
    b=source(tmp_path,merchant='omega cafe',approval='B')
    assert sw.choose_target(row(merchant='omega cafe'),sw.targets_from([a,b],[]),0)[0]==1


def test_unreadable_list_row_is_not_a_global_competitor(tmp_path):
    target=sw.Target(source(tmp_path)); good=row(); unknown=row(1,None,None)
    assert not sw.known_competitor(good,target,[good,unknown],1)
    assert sw.known_competitor(good,target,[good,row(1)],1)


class FakeDriver:
    def __init__(self, rows):
        self.data={r.expense_id:deepcopy(r) for r in rows}
        self.actions=[]; self.comments={}; self.fail=set(); self.fail_upload=False
    def guard(self): pass
    def inspect(self, seed):
        self.actions.append(('open',seed.expense_id))
        if seed.expense_id in self.fail: raise ar.AttachError('unreadable detail')
        return deepcopy(self.data[seed.expense_id])
    def receipt_row(self,r): return deepcopy(self.data[r.expense_id])
    def receipt_present(self,r,s): return self.data[r.expense_id].has_receipt
    def attach(self,s,r):
        self.actions.append(('attach',r.expense_id))
        if self.fail_upload: raise ar.AttachError('save outcome unknown')
        self.data[r.expense_id].has_receipt=True
    def verify_edit(self,p): return self.comments.get(p.row.expense_id)==p.comment
    def apply_edit(self,p):
        self.actions.append(('edit',p.row.expense_id)); self.comments[p.row.expense_id]=p.comment


def setup(monkeypatch,tmp_path,slips,entries,rows,details=None,confirm=True):
    monkeypatch.setattr(ar,'load_manifest',lambda *a:slips)
    monkeypatch.setattr(sheet,'load',lambda *a:entries)
    monkeypatch.setattr(sw.console,'confirm_action',lambda *a:confirm)
    driver=FakeDriver(details if details is not None else rows)
    monkeypatch.setattr(sw,'DetailDriver',lambda *a:driver)
    report=Report(URL,report_key(URL),'Synthetic report',tuple(rows))
    page=SimpleNamespace(url=URL)
    def run(apply=True,limit=None,again=False):
        return sw.run(page,report,tmp_path,deepcopy(settings.DEFAULTS),tmp_path/'workbook.json',apply,limit,again)
    return run,driver


def test_finish_one_expense_before_next_and_keep_existing_receipt(monkeypatch,tmp_path):
    a=source(tmp_path); b=source(tmp_path,amount=8000,approval='B')
    run,driver=setup(monkeypatch,tmp_path,[a,b],[entry(a),entry(b)],
                     [row(),row(1,amount=8000,receipt=True)])
    result=run()
    assert result==0
    assert driver.actions==[('open','E0'),('attach','E0'),('edit','E0'),('open','E1'),('edit','E1')]
    assert result.stats['kept']==1
    assert all(v['state']=='verified' for k,v in Journal(tmp_path/'concur-progress.json').data['tasks'].items() if '|expense|' in k)


def test_partial_list_values_read_from_detail(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(day=None,amount=None)],details=[row()])
    assert run()==0 and ('edit','E0') in driver.actions


def test_unreadable_expense_does_not_block_next(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(0,None,None),row(1)])
    driver.fail.add('E0')
    result=run()
    assert result==1 and ('edit','E1') in driver.actions and result.stats['held']==1


def test_unmatched_expense_untouched(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(0,amount=100),row(1)])
    assert run()==0 and driver.actions[0]==('open','E0')
    assert ('attach','E0') not in driver.actions and ('edit','E0') not in driver.actions


def test_known_duplicate_expenses_both_held(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(),row(1)])
    assert run()==1 and all(a[0]=='open' for a in driver.actions)


def test_cancel_does_not_visit_or_write_journal(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row()],confirm=False)
    assert run()==0 and driver.actions==[] and not (tmp_path/'concur-progress.json').exists()


def test_preview_reads_details_but_no_writes(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row()])
    assert run(apply=False)==0 and driver.actions==[('open','E0')]
    assert not (tmp_path/'concur-progress.json').exists()


def test_rerun_verifies_without_reupload_or_rewrite(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row()])
    assert run()==0 and run()==0
    assert driver.actions.count(('attach','E0'))==1 and driver.actions.count(('edit','E0'))==1


def test_unknown_upload_not_retried_and_no_followup_edit(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row()]);driver.fail_upload=True
    assert run()==1 and run()==1
    assert driver.actions.count(('attach','E0'))==1
    assert ('edit','E0') not in driver.actions


def test_receipt_unknown_does_not_upload_but_can_edit(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(receipt=None)])
    assert run()==1 and ('attach','E0') not in driver.actions and ('edit','E0') in driver.actions


def test_duplicate_id_never_opened(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(),row()])
    assert run()==1 and not driver.actions


def test_existing_receipt_only_no_upload_even_different_filename(monkeypatch,tmp_path):
    s=source(tmp_path); r=row(receipt=True);r.receipt_file='another.pdf'
    run,driver=setup(monkeypatch,tmp_path,[s],[],[r])
    assert run()==0 and driver.actions==[('open','E0')]


def test_no_forced_overwrite_option(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row()])
    with pytest.raises(ar.AttachError,match='again'):run(again=True)
    assert not driver.actions


def test_context_loss_stops_before_next(monkeypatch,tmp_path):
    s=source(tmp_path)
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row(),row(1,amount=999)])
    driver.inspect=Mock(side_effect=ContextChanged('login changed'))
    with pytest.raises(ContextChanged):run()
    assert driver.inspect.call_count==1


def test_receipt_race_never_overwrites(tmp_path):
    driver=DetailDriver(Mock(),URL,tmp_path)
    driver.receipt_row=Mock(return_value=row(receipt=True))
    driver.open_for_verification=Mock()
    with pytest.raises(ar.AttachError,match='영수증'):driver.attach(source(tmp_path),row())
    driver.open_for_verification.assert_not_called()
    driver.page.set_input_files.assert_not_called()


def test_per_stage_limits_kept(monkeypatch,tmp_path):
    a=source(tmp_path); b=source(tmp_path,amount=5000,approval='B')
    run,driver=setup(monkeypatch,tmp_path,[a,b],[entry(a),entry(b)],[row(),row(1,amount=5000)])
    result=run(limit=1)
    assert result.stats['limited']==1
    assert sum(a[0]=='attach' for a in driver.actions)==1 and sum(a[0]=='edit' for a in driver.actions)==1


def test_persisted_binding_prevents_reassignment_to_different_id(monkeypatch,tmp_path):
    s=source(tmp_path);t=sw.Target(s)
    j=Journal(tmp_path/'concur-progress.json')
    j.set(Task(task_key(URL,'OLD','expense',t.identity()),'Previous',None,None),'verified')
    run,driver=setup(monkeypatch,tmp_path,[s],[entry(s)],[row()])
    assert run()==1 and driver.actions==[('open','E0')]
