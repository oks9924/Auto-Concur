"""Concur 화면 값 해석. 날짜 순서나 통화가 모호하면 추측하지 않는다."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
import unicodedata


def clean(value):
    text = unicodedata.normalize('NFKC', str('' if value is None else value))
    text = re.sub('[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]', '', text)
    return text.replace('\u2212', '-').replace('\u2011', '-').strip()


def _date(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def date_options(value):
    """가능한 날짜와 해석 순서. 연도 없는 화면 값에는 현재 연도를 주입하지 않는다."""
    if isinstance(value, datetime):
        return {value.date(): 'YMD'}
    if isinstance(value, date):
        return {value: 'YMD'}
    text = clean(value)
    hit = re.search(r'(?<!\d)(\d{4})\s*[-/.년]\s*(\d{1,2})\s*[-/.월]\s*(\d{1,2})(?!\d)', text)
    if hit:
        parsed = _date(*hit.groups())
        return {parsed: 'YMD'} if parsed else {}
    hit = re.fullmatch(r'(\d{4})(\d{2})(\d{2})', text)
    if hit:
        parsed = _date(*hit.groups())
        return {parsed: 'YMD'} if parsed else {}
    hit = re.search(r'(?<!\d)(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})(?!\d)', text)
    if hit:
        first, second, year = hit.groups()
        options = {}
        for month, day, order in ((first, second, 'MDY'), (second, first, 'DMY')):
            parsed = _date(year, month, day)
            if parsed:
                options[parsed] = 'SAME' if parsed in options else order
        return options
    months = {name: i for i, name in enumerate(
        ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
    names = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    for pattern, day_first in ((names + r'\s+(\d{1,2}),?\s+(\d{4})', False),
                               (r'(\d{1,2})\s+' + names + r',?\s+(\d{4})', True)):
        hit = re.search(pattern, text, flags=re.I)
        if hit:
            a, b, year = hit.groups()
            name, day = (b, a) if day_first else (a, b)
            parsed = _date(year, months[name[:3].lower()], day)
            return {parsed: 'NAMED'} if parsed else {}
    return {}


def resolve_dates(raw_rows):
    """동일 목록의 일관된 날짜 순서 또는 같은 셀의 ISO 증거로만 해석한다."""
    options, orders = [], set()
    for raw in raw_rows:
        primary = date_options(raw.get('date'))
        extra = [date_options(raw.get(key)) for key in ('dateISO', 'dateLabel', 'label')]
        available = [x for x in [primary, *extra] if x]
        candidates = set.intersection(*(set(x) for x in available)) if available else set()
        if clean(raw.get('date')) and not primary:
            candidates = set()
        if len(candidates) == 1 and primary:
            order = primary.get(next(iter(candidates)))
            if order in ('MDY', 'DMY'):
                orders.add(order)
        options.append((candidates, primary))
    order = next(iter(orders)) if len(orders) == 1 else None
    resolved = []
    for candidates, primary in options:
        if len(candidates) > 1 and order:
            candidates = {d for d in candidates if primary.get(d) == order}
        resolved.append(next(iter(candidates)) if len(candidates) == 1 else None)
    return resolved


def parse_amount(value):
    """정수 KRW/통화 미표기만 지원. 소수 원 단위는 반올림하지 않는다."""
    text = clean(value)
    if not text:
        return None
    codes = re.findall(r'\b[A-Z]{3}\b', text.upper())
    if any(code != 'KRW' for code in codes) or any(symbol in text for symbol in '$€£¥'):
        return None
    text = re.sub(r'(?i)\bKRW\b', '', text).replace('₩', '').replace('원', '').strip()
    negative = text.startswith('(') and text.endswith(')')
    if negative:
        text = text[1:-1].strip()
    if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,3}(?:\s\d{3})+)(?:\.0{1,2})?', text):
        return None
    try:
        amount = Decimal(re.sub(r'[,\s]', '', text))
        if amount != amount.to_integral_value():
            return None
        return -int(amount) if negative else int(amount)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def amount_from_summary(value):
    text = clean(value)
    hits = re.findall(r'(?:KRW|₩)\s*([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)', text, flags=re.I)
    values = [parse_amount(hit) for hit in hits]
    return values[0] if len(values) == 1 else None
