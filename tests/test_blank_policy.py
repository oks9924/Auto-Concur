from datetime import date
from unittest.mock import Mock

from src import attach_receipts as ar, fix_expenses as fx, sheet, settings


def test_transit_ignores_stale_purpose_in_plan_and_execution(monkeypatch, tmp_path):
    entry = sheet.SheetRow(date(2026, 8, 1), 1000, '', 'A', sheet.TRANSIT_TYPE,
                           'old purpose', '출장 택시', '')
    monkeypatch.setattr(sheet, 'load', lambda p: [entry])
    row = ar.Row(0, entry.when, 1000, '', 'E1', sheet.TRANSIT_TYPE)
    plans, _, _ = fx.plans_from_sheet(settings.DEFAULTS, [row], tmp_path, 1)
    plan = plans[0][0]
    assert plan.purpose == '' and '목적' not in plan.summary()
    assert entry.purpose == 'old purpose'
    monkeypatch.setattr(fx, '_wait_js', Mock())
    fields = Mock(return_value=True)
    monkeypatch.setattr(fx, '_set_field', fields)
    monkeypatch.setattr(fx, '_sync_attendees', Mock(return_value=0))
    save = Mock()
    monkeypatch.setattr(fx, '_save_expense', save)
    assert fx.apply_plan(Mock(), plan, 'https://example.com/reports/R') == '코멘트'
    assert fields.call_count == 1
    assert fields.call_args.args[1] == fx.COMMENT_FIELD
    save.assert_called_once()


def test_transit_purpose_only_does_not_create_work(monkeypatch, tmp_path):
    entry = sheet.SheetRow(date(2026, 8, 1), 1000, '', 'A', sheet.TRANSIT_TYPE,
                           'old purpose', '', '')
    monkeypatch.setattr(sheet, 'load', lambda p: [entry])
    row = ar.Row(0, entry.when, 1000, '', 'E1', sheet.TRANSIT_TYPE)
    assert fx.plans_from_sheet(settings.DEFAULTS, [row], tmp_path, 1)[0] == []


def test_transit_type_change_by_code_keeps_only_description():
    row = ar.Row(0, date(2026, 8, 1), 1000, '', 'E1', 'Other')
    plan = fx.Plan(row, 'TRAIN', 'Transit', 'old purpose', '택시', 'old attendee')
    assert plan.purpose == plan.attendee == ''
    assert plan.comment == '택시'
    meal = fx.Plan(row, None, sheet.ATTENDEE_REQUIRED_TYPE, 'meeting', 'meal')
    assert meal.purpose == 'meeting'


def test_all_blank_fields_produce_no_edit_plan(monkeypatch, tmp_path):
    entry = sheet.SheetRow(date(2026, 8, 1), 1000, '', 'A', '', '', '', '')
    monkeypatch.setattr(sheet, 'load', lambda p: [entry])
    row = ar.Row(0, entry.when, 1000, '', 'E1', '내부 직원간 식음료')
    cfg = {**settings.DEFAULTS, 'attendee_default': 'do.not.add'}
    plans, gaps, missing = fx.plans_from_sheet(cfg, [row], tmp_path, 1)
    assert plans == gaps == missing == []


def test_lodging_location_only_does_not_edit_dates_or_itemization(monkeypatch):
    row = ar.Row(0, date(2026, 8, 1), 1000, '', 'E1', '숙박비')
    plan = fx.Plan(row, None, '숙박비', lodging=fx.Lodging(None, None, '국내', ''))
    monkeypatch.setattr(fx, '_open_tab', Mock())
    dates = Mock()
    itemization = Mock()
    combo = Mock(return_value=True)
    save = Mock()
    monkeypatch.setattr(fx, '_set_date_range', dates)
    monkeypatch.setattr(fx, '_itemization_ready', itemization)
    monkeypatch.setattr(fx, '_pick_from_combo', combo)
    monkeypatch.setattr(fx, '_save_expense', save)
    assert fx._apply_lodging(Mock(), plan, 'report') == ['숙박 위치']
    dates.assert_not_called()
    itemization.assert_not_called()
    assert combo.call_count == save.call_count == 1


def test_receipts_use_screen_even_with_local_done_record(monkeypatch, tmp_path):
    slips = [ar.Slip(tmp_path / 'a.pdf', date(2026, 8, 1), 1000, '', 'A'),
             ar.Slip(tmp_path / 'b.pdf', date(2026, 8, 1), 1000, '', 'B')]
    rows = [ar.Row(0, slips[0].when, 1000, '', 'E1', has_receipt=True, receipt_file='a.pdf'),
            ar.Row(1, slips[0].when, 1000, '', 'E2', has_receipt=False)]
    monkeypatch.setattr(ar, 'load_manifest', lambda p: slips)
    monkeypatch.setattr(ar, 'rows_when_ready', lambda p: rows)
    ar.done_path(tmp_path).write_text('A\nB\n', encoding='utf-8')
    attach = Mock()
    monkeypatch.setattr(ar, 'attach', attach)
    assert ar.attach_phase(Mock(), 'report', tmp_path, True, 1, None) == 0
    assert attach.call_count == 1
    assert attach.call_args.args[1:3] == (slips[1], rows[1])
