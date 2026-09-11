from datetime import date
from unittest.mock import Mock
import pytest
from src import concur_workflow as wf, attach_receipts as ar, console, settings, sheet
from src.report_session import Report, report_key

URL = 'https://eu2.concursolutions.com/nui/expense/reports/R1'


def test_unmatched_counts_transactions_not_stages(tmp_path):
    when = date(2026, 9, 9)
    slip = ar.Slip(tmp_path / 'a.pdf', when, 25300, '', 'A')
    entry = sheet.SheetRow(when, 25300, '', 'A', '', '', '', '')
    other = sheet.SheetRow(when, 25300, '', 'B', '', '', '', '')
    result = wf.unmatched_transactions([(slip, '후보 없음')], [(entry, '후보 없음'), (other, '후보 없음')])
    assert len(result) == 2
    assert '영수증' in result[0] and '입력' in result[0]


def test_unknown_approval_does_not_merge_distinct_transactions(tmp_path):
    slip = ar.Slip(tmp_path / 'a.pdf', date(2026, 9, 9), 1000, '', '')
    assert len(wf.unmatched_transactions([(slip, '없음'), (slip, '없음')], [])) == 2


def setup_plan(monkeypatch, tmp_path):
    file = tmp_path / 'a.pdf'
    file.write_bytes(b'sample')
    slip = ar.Slip(file, date(2026, 8, 1), 1000, '', 'A')
    row = ar.Row(0, slip.when, 1000, '', 'E1', has_receipt=False)
    report = Report(URL, report_key(URL), '8월 경비', (row,))
    monkeypatch.setattr(ar, 'load_manifest', lambda folder: [slip])
    return report, slip, row


def test_plan_is_read_only_and_retains_receipt_only_rows(monkeypatch, tmp_path):
    report, slip, row = setup_plan(monkeypatch, tmp_path)
    driver = Mock()
    plan = wf.build_plan(report, tmp_path, settings.DEFAULTS, None, driver=driver)
    assert plan.receipts == 1 and plan.edits == 0
    assert not driver.mock_calls


def test_cancel_and_preview_do_not_mutate_or_write_journal(monkeypatch, tmp_path):
    report, _, _ = setup_plan(monkeypatch, tmp_path)
    driver = Mock()
    monkeypatch.setattr(wf, 'Driver', lambda *args: driver)
    confirm = Mock(return_value=False)
    monkeypatch.setattr(console, 'confirm_action', confirm)
    assert wf.run(Mock(), report, tmp_path, settings.DEFAULTS, None, False) == 0
    confirm.assert_not_called()
    assert wf.run(Mock(), report, tmp_path, settings.DEFAULTS, None, True) == 0
    assert not driver.mock_calls
    assert not (tmp_path / 'concur-progress.json').exists()


def test_confirmation_revalidates_before_any_operation(monkeypatch, tmp_path):
    report, _, _ = setup_plan(monkeypatch, tmp_path)
    driver = Mock()
    monkeypatch.setattr(wf, 'Driver', lambda *args: driver)
    monkeypatch.setattr(console, 'confirm_action', lambda *args: True)
    monkeypatch.setattr(wf, 'revalidate', Mock(side_effect=ar.AttachError('changed')))
    with pytest.raises(ar.AttachError):
        wf.run(Mock(), report, tmp_path, settings.DEFAULTS, None, True)
    assert not driver.mock_calls


def test_workflow_verifies_and_reuses_journal(monkeypatch, tmp_path):
    report, _, _ = setup_plan(monkeypatch, tmp_path)
    driver = Mock()
    driver.receipt_present.side_effect = [False, True, True]
    monkeypatch.setattr(wf, 'Driver', lambda *args: driver)
    monkeypatch.setattr(console, 'confirm_action', lambda *args: True)
    monkeypatch.setattr(wf, 'revalidate', Mock())
    assert wf.run(Mock(), report, tmp_path, settings.DEFAULTS, None, True) == 0
    assert wf.run(Mock(), report, tmp_path, settings.DEFAULTS, None, True) == 0
    driver.attach.assert_called_once()
