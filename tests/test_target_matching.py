"""대상별 제외/보류 정책. 실제 영수증이나 Concur 로그인 없이 검증한다."""
from dataclasses import replace
from datetime import date
from itertools import permutations
from pathlib import Path
from unittest.mock import Mock
import pytest
from src import attach_receipts as ar, concur_workflow as wf, report_session as rs, settings, sheet, console
from src.concur_driver import Driver
from src.target_matching import match_sources

WHEN = date(2026, 9, 17)
URL = 'https://eu2.concursolutions.com/nui/expense/reports/TEST'


def slip(amount=1000, when=WHEN, approval='A', merchant=''):
    return ar.Slip(Path(approval + '.pdf'), when, amount, merchant, approval)


def row(amount=1000, when=WHEN, eid='E1', vendor='', index=0):
    return ar.Row(index, when, amount, '', eid, vendor=vendor, has_receipt=False)


def entry(s):
    return sheet.SheetRow(s.when, s.amount, s.merchant, s.approval, '', '', '입력', '')


@pytest.mark.parametrize('bad', [row(9999, None, None), row(None, date(2026, 1, 1), None),
                                  row(0, None, 'manual'), row(-1000, None, 'manual')])
def test_unrelated_incomplete_row_does_not_block(bad):
    pairs, missed = ar.match([slip()], [bad, row()], 1)
    assert len(pairs) == 1 and not missed
    batch = match_sources([slip()], [], [bad, row()], 1)
    assert batch.excluded_rows == [bad]


@pytest.mark.parametrize('bad', [row(1000, None, 'manual'), row(None, WHEN, 'manual'),
                                  row(1000, WHEN, None)])
def test_only_potentially_related_target_is_held(bad):
    unrelated = slip(2000, date(2026, 1, 1), 'B')
    pairs, missed = ar.match([slip(), unrelated], [row(), bad, row(2000, unrelated.when, 'E2')], 1)
    assert [s.approval for s, _, _ in pairs] == ['B']
    assert [s.approval for s, _ in missed] == ['A']
    assert '보류' in missed[0][1]


def test_unknown_date_and_amount_are_not_assumed_unrelated():
    targets = [slip(), slip(2000, approval='B')]
    pairs, missed = ar.match(targets, [row(), row(2000, eid='E2'), row(None, None, 'manual')], 1)
    assert not pairs and len(missed) == 2


def test_manual_but_complete_row_is_an_ordinary_candidate():
    pairs, missed = ar.match([slip()], [row(eid='manual-entry')], 1)
    assert len(pairs) == 1 and not missed


@pytest.mark.parametrize('amount', [0, -1000, 1000])
def test_zero_and_signed_amounts_are_preserved(amount):
    pairs, missed = ar.match([slip(amount)], [row(amount)], 0)
    assert pairs[0][1].amount == amount and not missed


def test_boundary_of_tolerance_is_related():
    bad = row(None, date(2026, 9, 18), 'manual')
    assert ar.match([slip()], [row(), bad], 1)[1]
    assert not ar.match([slip()], [row(), bad], 0)[1]


def test_duplicate_ids_hold_only_the_affected_target():
    targets = [slip(), slip(2000, approval='B')]
    rows = [row(), row(9999, eid='E1'), row(2000, eid='E2')]
    pairs, missed = ar.match(targets, rows, 1)
    assert [s.approval for s, _, _ in pairs] == ['B']
    assert 'ID 중복' in missed[0][1]


def test_missing_ids_on_unrelated_rows_do_not_block_valid_rows():
    rows = [row(9000, None, None), row(8000, None, None), row()]
    assert len(ar.match([slip()], rows, 1)[0]) == 1


@pytest.mark.parametrize('problem', ['목록 로딩 중', '표시된 행이 전체 행 수보다 적음'])
def test_structurally_partial_list_is_never_treated_as_unrelated(problem):
    rows = [row(), replace(row(9999, None, 'other'), read_problem=problem)]
    assert not ar.match([slip()], rows, 1)[0]


def test_ambiguous_candidate_does_not_get_assigned_by_order():
    for rows in permutations([row(eid='E1'), row(eid='E2')]):
        pairs, missed = ar.match([slip()], list(rows), 1)
        assert not pairs and '구별' in missed[0][1]


def test_competing_targets_are_not_first_come_first_served():
    for targets in permutations([slip(), slip(approval='B')]):
        pairs, missed = ar.match(list(targets), [row()], 1)
        assert not pairs and len(missed) == 2


def test_unresolved_target_keeps_its_candidates_reserved():
    # A can be either E1 or E2; B can only be E1. Do not let B steal E1.
    targets = [slip(when=date(2026, 9, 17)), slip(when=date(2026, 9, 15), approval='B')]
    rows = [row(when=date(2026, 9, 16)), row(when=date(2026, 9, 18), eid='E2')]
    for ts in permutations(targets):
        for rs_ in permutations(rows):
            assert not ar.match(list(ts), list(rs_), 1)[0]


def test_strong_vendor_evidence_is_order_independent():
    targets = [slip(merchant='ALPHA CAFE'), slip(approval='B', merchant='BETA RESTAURANT')]
    rows = [row(eid='E1', vendor='ALPHA CAFE'), row(eid='E2', vendor='BETA RESTAURANT')]
    for ts in permutations(targets):
        for rs_ in permutations(rows):
            pairs, missed = ar.match(list(ts), list(rs_), 1)
            assert not missed
            assert {s.approval: r.expense_id for s, r, _ in pairs} == {'A': 'E1', 'B': 'E2'}


def test_incomplete_candidate_not_eliminated_by_vendor_similarity():
    pairs, missed = ar.match([slip(merchant='라한호텔울산')],
        [row(vendor='RA HAN HO TEL UL SAN'), row(None, WHEN, 'manual', vendor='UNKNOWN')], 1)
    assert not pairs and missed


def test_both_stages_use_one_mapping_for_same_transaction():
    s = slip()
    batch = match_sources([s], [entry(s)], [row()], 1)
    assert len(batch.targets) == 1
    assert batch.receipt_pairs[0][1] is batch.edit_pairs[0][1]
    assert not batch.receipt_missing and not batch.edit_missing


def test_cross_stage_competing_transactions_are_both_held():
    batch = match_sources([slip()], [entry(slip(approval='B'))], [row()], 1)
    assert not batch.receipt_pairs and not batch.edit_pairs
    assert batch.receipt_missing and batch.edit_missing


@pytest.mark.parametrize('change', [{'approval': ''}, {'merchant': 'different'}, {'approval': 'B'}])
def test_unknown_or_conflicting_source_identity_not_silently_merged(change):
    s = slip(approval='' if change == {'approval': ''} else 'A')
    batch = match_sources([s], [entry(replace(s, **change))], [row()], 1)
    assert len(batch.targets) == 2 and not batch.receipt_pairs


def test_duplicate_within_one_source_is_not_deduplicated():
    s = slip()
    batch = match_sources([s, s], [entry(s)], [row()], 1)
    assert len(batch.targets) == 3
    assert not batch.receipt_pairs and not batch.edit_pairs


def test_verification_rechecks_new_ambiguous_rows_without_using_old_mapping():
    s, expected = slip(), row()
    batch = match_sources([s], [], [expected], 1)
    assert batch.verify(expected, [expected, row(9999, None, 'manual')])
    assert not batch.verify(expected, [expected, row(1000, None, 'manual')])
    assert not batch.verify(expected, [row(eid='different')])
    assert not batch.verify(expected, [replace(expected, amount=None)])


def test_driver_retains_incomplete_rows_on_requery(monkeypatch, tmp_path):
    current = [row(), row(9000, None, 'manual')]
    read = Mock(return_value=current)
    monkeypatch.setattr(ar, 'rows_when_ready', read)
    driver = Driver(Mock(url=URL), URL, tmp_path)
    driver.matching = match_sources([slip()], [], current, 1)
    assert driver.receipt_present(row(), slip()) is False
    assert read.call_args.kwargs['allow_incomplete'] is True


def test_driver_holds_new_ambiguity_before_receipt_or_edit(monkeypatch, tmp_path):
    driver = Driver(Mock(url=URL), URL, tmp_path)
    driver.matching = match_sources([slip()], [], [row()], 1)
    monkeypatch.setattr(driver, 'rows', lambda: [row(), row(1000, None, 'manual')])
    with pytest.raises(ar.AttachError, match='보류'):
        driver.receipt_present(row(), slip())
    plan = Mock(row=row())
    with pytest.raises(ar.AttachError, match='보류'):
        driver.verify_edit(plan)
    assert not driver.page.goto.called


def test_report_detection_accepts_stable_incomplete_rows(monkeypatch):
    page = Mock(url=URL)
    page.is_closed.return_value = False
    page.evaluate.return_value = 'TEST'
    rows = [row(), row(9000, None, 'manual')]
    read = Mock(return_value=rows)
    monkeypatch.setattr(ar, 'read_rows', read)
    assert rs.ready_report(page, timeout=2).rows == tuple(rows)
    assert read.call_count == 4


def test_incomplete_raw_value_change_invalidates_start_confirmation(monkeypatch):
    original = replace(row(9000, None, 'manual'), raw_date='invalid-a')
    report = rs.Report(URL, rs.report_key(URL), 'TEST', (row(), original))
    monkeypatch.setattr(ar, 'rows_when_ready', lambda *a, **kw: [row(), replace(original, raw_date='invalid-b')])
    with pytest.raises(ar.AttachError, match='바뀌었습니다'):
        rs.revalidate(Mock(url=URL), report)


def test_partial_plan_runs_healthy_target_only(monkeypatch, tmp_path):
    good = replace(slip(2000, approval='B'), path=tmp_path/'good.pdf')
    good.path.write_bytes(b'fixture')
    bad = slip()
    rows = [row(), row(1000, None, 'manual'), row(2000, eid='E2'), row(9999, None, 'other')]
    report = rs.Report(URL, rs.report_key(URL), 'TEST', tuple(rows))
    monkeypatch.setattr(ar, 'load_manifest', lambda _: [bad, good])
    driver = Mock()
    driver.receipt_present.side_effect = [False, True]
    monkeypatch.setattr(wf, 'Driver', lambda *a: driver)
    monkeypatch.setattr(console, 'confirm_action', lambda *a: True)
    monkeypatch.setattr(wf, 'revalidate', lambda *a: None)
    result = wf.run(Mock(), report, tmp_path, settings.DEFAULTS, None, True)
    assert result == 1  # 부분 완료를 전체 성공으로 표시하지 않는다.
    driver.attach.assert_called_once()
    assert driver.attach.call_args.args[0] is good
    assert '모두 반영 확인' in result.summary and '1건' in result.summary
    assert (tmp_path/'concur-progress.json').exists()


def test_limit_is_applied_after_ambiguity_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(ar, 'load_manifest', lambda _: [slip(), slip(approval='B')])
    report = rs.Report(URL, rs.report_key(URL), 'TEST', (row(),))
    plan = wf.build_plan(report, tmp_path, settings.DEFAULTS, None, limit=1, driver=Mock())
    assert not plan.tasks and len(plan.unmatched) == 2


def test_workflow_does_not_match_receipts_and_edits_independently(monkeypatch, tmp_path):
    s = slip()
    monkeypatch.setattr(ar, 'load_manifest', lambda _: [s])
    monkeypatch.setattr(sheet, 'load', lambda _: [entry(slip(approval='B'))])
    report = rs.Report(URL, rs.report_key(URL), 'TEST', (row(),))
    plan = wf.build_plan(report, tmp_path, settings.DEFAULTS, tmp_path/'workbook.json', driver=Mock())
    assert not plan.tasks and len(plan.unmatched) == 2
