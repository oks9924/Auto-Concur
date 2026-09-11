from datetime import date
from unittest.mock import Mock
import pytest
from src import report_session as rs, attach_receipts as ar

URL = 'https://eu2.concursolutions.com/nui/expense/reports/R1'


@pytest.mark.parametrize('url', ['https://evil.example/nui/expense/reports/R1',
    URL + '/expenses/E1', 'https://eu2.concursolutions.com/login',
    'https://eu2.concursolutions.com.evil.example/nui/expense/reports/R1'])
def test_login_detail_and_other_hosts_not_ready(url):
    assert rs.report_key(url) is None


@pytest.mark.parametrize('suffix', ['', '/expenses'])
def test_auto_detection_waits_for_complete_stable_rows(monkeypatch, suffix):
    page = Mock(url=URL + suffix)
    page.is_closed.return_value = False
    page.title.return_value = '8월 경비'
    page.evaluate.return_value = '8월 경비'
    row = ar.Row(0, date(2026, 8, 1), 1000, '', 'E1')
    read = Mock(side_effect=[[], [ar.Row(0, None, None, '', 'E1')], [row], [row], [row], [row]])
    monkeypatch.setattr(ar, 'read_rows', read)
    report = rs.ready_report(page, timeout=3)
    assert report.rows == (row,)
    assert report.url == URL
    assert read.call_count == 6


def test_confirmation_does_not_override_changed_report(monkeypatch):
    row = ar.Row(0, date(2026, 8, 1), 1000, '', 'E1')
    report = rs.Report(URL, rs.report_key(URL), 'Report', (row,))
    with pytest.raises(ar.AttachError):
        rs.revalidate(Mock(url=URL.replace('R1', 'R2')), report)
    monkeypatch.setattr(ar, 'rows_when_ready', lambda page: [])
    with pytest.raises(ar.AttachError):
        rs.revalidate(Mock(url=URL), report)


def test_guard_accepts_current_detail_only():
    rs.check_context(Mock(url=URL + '/expenses/E1?modal=attendees'), URL)
    with pytest.raises(ar.AttachError):
        rs.check_context(Mock(url='https://login.example'), URL)
