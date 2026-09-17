"""Concur display values. Keep uncertainty rather than changing a transaction's meaning."""
from datetime import date, datetime
import re
import unicodedata
from .concur_formats import DATE_ORDER, NUMBER_STYLE


def clean(value):
    text = unicodedata.normalize('NFKC', str('' if value is None else value))
    text = re.sub('[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]', '', text)
    return re.sub(r'\s+', ' ', text.replace('\u2212', '-').replace('\u2011', '-')).strip()


def _date(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


_MONTHS = {name: i for i, name in enumerate(
    ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'), 1)}
_NAMES = r'(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_PATTERNS = [
    (r'(?<!\d)(\d{4})\s*([/.-])\s*(\d{1,2})\s*\2\s*(\d{1,2})(?!\d)', 'YMD'),
    (r'(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일?', 'KO'),
    (r'(?<!\d)(\d{1,2})\s*([/.-])\s*(\d{1,2})\s*\2\s*(\d{4})(?!\d)', 'NUM'),
    (r'(?<!\w)' + _NAMES + r'\.?[ -]+(\d{1,2})(?:st|nd|rd|th)?,?[ -]+(\d{4})(?!\d)', 'MONTH'),
    (r'(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[ -]+' + _NAMES + r'\.?,?[ -]+(\d{4})(?!\d)', 'DAY'),
]


def date_options(value):
    """A full four-digit-year date. Multiple dates and invalid dates are not salvaged."""
    if isinstance(value, datetime):
        return {value.date(): 'YMD'}
    if isinstance(value, date):
        return {value: 'YMD'}
    text = clean(value)
    if re.fullmatch(r'\d{8}', text):
        parsed = _date(text[:4], text[4:6], text[6:])
        return {parsed: 'YMD'} if parsed else {}
    hits = [(hit, kind) for pattern, kind in _PATTERNS for hit in re.finditer(pattern, text, re.I)]
    if len(hits) != 1:
        return {}
    hit, kind = hits[0]
    if kind == 'YMD':
        parsed = _date(hit[1], hit[3], hit[4])
    elif kind == 'KO':
        parsed = _date(hit[1], hit[2], hit[3])
    elif kind == 'NUM':
        options = {}
        for m, d, order in ((hit[1], hit[3], 'MDY'), (hit[3], hit[1], 'DMY')):
            parsed = _date(hit[4], m, d)
            if parsed:
                options[parsed] = 'SAME' if parsed in options else order
        return options
    elif kind == 'MONTH':
        parsed = _date(hit[3], _MONTHS[hit[1][:3].lower()], hit[2])
    else:
        parsed = _date(hit[3], _MONTHS[hit[2][:3].lower()], hit[1])
    return {parsed: 'YMD' if kind in ('YMD','KO') else 'NAMED'} if parsed else {}


def resolve_dates(raw_rows, order=None):
    """Use same-cell evidence, consistent row evidence, or explicit per-run settings."""
    explicit = (order or DATE_ORDER.get()).upper()
    if explicit not in ('AUTO', 'YMD', 'MDY', 'DMY'):
        raise ValueError('지원하지 않는 날짜 순서입니다.')
    options, orders = [], set()
    for raw in raw_rows:
        primary = date_options(raw.get('date'))
        extra = [date_options(raw.get(key)) for key in ('dateISO', 'dateLabel', 'label')]
        available = [x for x in [primary, *extra] if x]
        candidates = set.intersection(*(set(x) for x in available)) if available else set()
        if clean(raw.get('date')) and not primary:
            candidates = set()
        if clean(raw.get('dateISO')) and not extra[0]:
            candidates = set()
        if explicit != 'AUTO' and primary and any(v in ('MDY','DMY','SAME') for v in primary.values()):
            candidates = {d for d in candidates if primary.get(d) in (explicit, 'SAME')}
        if len(candidates) == 1 and primary:
            detected = primary.get(next(iter(candidates)))
            if detected in ('MDY', 'DMY'):
                orders.add(detected)
        options.append((candidates, primary))
    inferred = next(iter(orders)) if len(orders) == 1 else None
    resolved = []
    for candidates, primary in options:
        if len(candidates) > 1 and inferred and explicit == 'AUTO':
            candidates = {d for d in candidates if primary.get(d) == inferred}
        resolved.append(next(iter(candidates)) if len(candidates) == 1 else None)
    return resolved


def money_pattern(style=None):
    style = style or NUMBER_STYLE.get()
    if style not in ('DOT', 'COMMA'):
        raise ValueError('지원하지 않는 금액 서식입니다.')
    group, decimal = (',', r'\.') if style == 'DOT' else (r'\.', ',')
    return r'(?:[0-9]+|[0-9]{1,3}(?:' + group + r'[0-9]{3})+|[0-9]{1,3}(?: [0-9]{3})+)(?:' + decimal + r'0{1,2})?'


def parse_amount(value, style=None):
    """Exact integer KRW. Currency/sign/grouping errors and fractional won stay unknown."""
    style = style or NUMBER_STYLE.get()
    text = clean(value)
    text = re.sub('KRW', '', text, flags=re.I).replace('₩', '').replace('원', '').strip()
    negative = False
    if text.startswith('(') and text.endswith(')'):
        negative, text = True, text[1:-1].strip()
    elif text.endswith('-'):
        negative, text = True, text[:-1].strip()
    elif text[:1] in ('+', '-'):
        negative, text = text[0] == '-', text[1:].strip()
    if not re.fullmatch(money_pattern(style), text):
        return None
    integral = text.split(',' if style == 'COMMA' else '.')[0]
    try:
        amount = int(re.sub('[,. ]', '', integral))
        return -amount if negative else amount
    except (ValueError, OverflowError):
        return None


def amount_from_summary(value):
    """Consume the complete currency-marked number, not a valid-looking prefix."""
    text = clean(value)
    number = r'[0-9](?:[0-9., ]*[0-9])?'
    prefix = r'(?:\(\s*)?(?:[+-]\s*)?(?:\b[A-Z]{3}\b|[₩$€£¥])\s*(?:\(\s*)?(?:[+-]\s*)?' + number + r'(?:\s*\))?-?'
    suffix = r'(?:\(\s*)?(?:[+-]\s*)?' + number + r'\s*(?:원|\bKRW\b)(?:\s*\))?-?'
    hits = list(re.finditer(prefix + '|' + suffix, text, re.I))
    if len(hits) != 1:
        return None
    hit = hits[0]
    tail = text[hit.end():]
    if re.match(r"(?:['’][0-9]|[A-Za-z][0-9])", tail):
        return None
    return parse_amount(hit[0])


def same_text(actual, expected):
    """Compare presentation whitespace without modifying persisted user content."""
    def normalize(value):
        return unicodedata.normalize('NFC', str(value)).replace('\r\n', '\n').replace('\r', '\n').replace('\xa0', ' ').replace('\u202f', ' ').strip()
    return normalize(actual) == normalize(expected)
