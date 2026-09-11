"""날짜 입력 해석. 모호하거나 존재하지 않는 날짜는 추측하지 않는다."""
from datetime import date, datetime, timedelta
import re
import unicodedata


def parse_date(value, year=None):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = unicodedata.normalize('NFKC', str(value)).strip()
    if not text:
        return None
    if re.fullmatch(r'\d{5}(?:\.0+)?', text):
        candidate = date(1899, 12, 30) + timedelta(days=int(float(text)))
        return candidate if 2000 <= candidate.year <= 2099 else None
    # 날짜 뒤의 시각은 ISO 형식일 때만 허용한다. 임의의 뒷부분을 잘라내지 않는다.
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    text = re.sub(r'\s*\([월화수목금토일](?:요일)?\)\s*$', '', text)
    text = re.sub(r'\s+', '', text)
    text = text.replace('년', '-').replace('월', '-').replace('일', '')
    match = re.fullmatch(r'(\d{4}|\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\.?', text)
    if match:
        y, m, d = map(int, match.groups())
        if len(match[1]) == 2:
            y += 2000
    elif re.fullmatch(r'\d{8}|\d{6}', text):
        y, m, d = int(text[:-4]), int(text[-4:-2]), int(text[-2:])
        if len(text) == 6:
            y += 2000
    else:
        match = re.fullmatch(r'(\d{1,2})[-/.](\d{1,2})\.?', text)
        if not match or year is None:
            return None
        y, m, d = year, int(match[1]), int(match[2])
    try:
        return date(y, m, d)
    except ValueError:
        return None
