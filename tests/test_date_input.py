from datetime import date, datetime
import pytest
from src.date_input import parse_date
from src.worksheet import normalize


@pytest.mark.parametrize('value', ['2026-08-17', '2026.8.17', '2026/8/17', '20260817',
    '260817', '26/8/17', '8/17', '8.17', '8-17', '8월 17일', '2026년 8월 17일',
    '2026. 8. 17.', '2026-08-17 (월)', '2026-08-17T12:30:00+09:00',
    '46251', 46251.0, date(2026, 8, 17), datetime(2026, 8, 17, 12)])
def test_supported_formats(value):
    assert parse_date(value, 2026) == date(2026, 8, 17)


@pytest.mark.parametrize('value', ['2026-02-29', '2026-04-31', '20261301', '396000',
    '2026-08-17 wrong', '2026-08-17/2026-08-18', '17/08/2026', '', None])
def test_invalid_or_ambiguous_values_are_not_truncated(value):
    assert parse_date(value, 2026) is None


def test_leap_day_with_inferred_year_and_cross_year():
    assert parse_date('2/29', 2024) == date(2024, 2, 29)
    assert parse_date('2/29', 2026) is None
    result = normalize({'거래일': '2026-12-20', '입실날짜': '20261230', '퇴실날짜': '2027년 1월 2일'})
    assert result['입실날짜'] == '2026-12-30'
    assert result['퇴실날짜'] == '2027-01-02'
