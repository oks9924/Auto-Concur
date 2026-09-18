"""Local manual-mileage data, independent of card receipts and Concur execution."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unicodedata
from uuid import uuid4

from . import paths
from .date_input import parse_date

RATES = {'long': '470', 'short': '280'}  # User-provided rates, not a company policy assertion.
BOOK_NAME = 'mileage-workbook.json'
IMAGE_TYPES = {'PNG': '.png', 'JPEG': '.jpg'}
MAX_IMAGE = 20 * 1024 * 1024


def text(value):
    return str(value if value is not None else '').strip()


def clean_label(value, field, required=True):
    result = text(value)
    if required and not result:
        raise ValueError(f'{field}을(를) 입력하세요.')
    if len(result) > 500 or any(unicodedata.category(c).startswith('C') for c in result):
        raise ValueError(f'{field}: 500자 이내의 한 줄로 입력하세요.')
    return result


def number(value, field, integer=False, minimum=Decimal('0')):
    raw = unicodedata.normalize('NFKC', text(value))
    # Dot decimals only: do not silently interpret a comma as a thousands separator.
    if not raw or not all(c in '0123456789.' for c in raw) or raw.count('.') > 1:
        raise ValueError(f'{field}: 단위나 쉼표 없이 숫자로 입력하세요. 예: 12.5')
    try:
        value = Decimal(raw)
        if not value.is_finite() or value < minimum or (integer and value != value.to_integral_value()):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError(f'{field}: {minimum} 이상의 ' + ('정수' if integer else '숫자') + '를 입력하세요.') from None
    if len(raw) > 18:
        raise ValueError(f'{field}: 입력값이 너무 깁니다.')
    return format(value.normalize(), 'f')


def validate_vehicles(rows):
    if not isinstance(rows, list):
        raise ValueError('차량 목록 형식이 올바르지 않습니다.')
    result, seen = [], set()
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError('차량 항목 형식이 올바르지 않습니다.')
        label = clean_label(item.get('vehicle'), 'Concur 차량 ID')
        kind = text(item.get('kind'))
        if kind not in RATES:
            raise ValueError('차량 구분은 long 또는 short를 선택하세요.')
        if label.casefold() in seen:
            raise ValueError('같은 Concur 차량 ID가 중복되어 있습니다.')
        seen.add(label.casefold())
        result.append({'vehicle': label, 'kind': kind, 'rate': RATES[kind]})
    return result


def checked_row(raw):
    """Validate user input and keep the selected vehicle/rate snapshot."""
    result = deepcopy(raw)
    if not isinstance(result, dict):
        raise ValueError('마일리지 행 형식이 올바르지 않습니다.')
    ident = text(result.get('id'))
    if not ident or len(ident) > 64:
        raise ValueError('마일리지 내부 식별자가 올바르지 않습니다.')
    when = parse_date(result.get('date'))
    if not when:
        raise ValueError('거래 날짜를 연도까지 입력하거나 달력에서 선택하세요.')
    result['date'] = when.isoformat()
    for key, label in [('origin', '출발지'), ('destination', '도착지'), ('vehicle', '차량 ID')]:
        result[key] = clean_label(result.get(key), label)
    kind = text(result.get('kind'))
    if kind not in RATES or text(result.get('rate')) != RATES[kind]:
        raise ValueError('차량의 long/short 구분과 환급률을 다시 확인하세요.')
    result['distance'] = number(result.get('distance'), '거리(km)', minimum=Decimal('0.000001'))
    result['passengers'] = number(result.get('passengers'), '탑승자 수', integer=True)
    result['description'] = text(result.get('description'))
    if not result['description'] or len(result['description']) > 2000:
        raise ValueError('설명을 1~2000자로 입력하세요.')
    if not result.get('map') or not result.get('map_sha256'):
        raise ValueError('지도 이미지(PNG/JPG)를 첨부하세요.')
    # Estimate only. Concur may apply a different effective rate/rounding rule.
    result['estimate'] = format(Decimal(result['distance']) * Decimal(result['rate']), 'f')
    return result


class LocalRows:
    """Independent, atomic, optimistic local document with fail-closed loading."""
    def __init__(self, path, validator):
        self.path, self.validator = Path(path), validator
        self.version = self.digest()
        raw = json.loads(self.path.read_text(encoding='utf-8-sig')) if self.path.exists() else {'version': 1, 'rows': []}
        if not isinstance(raw, dict) or raw.get('version') != 1 or not isinstance(raw.get('rows'), list):
            raise ValueError(f'{self.path.name}: 지원하지 않는 형식입니다. 파일을 덮어쓰지 않았습니다.')
        self.rows = validator(raw['rows'])
        self.original = deepcopy(self.rows)

    def digest(self):
        return sha256(self.path.read_bytes()).hexdigest() if self.path.exists() else None

    @property
    def dirty(self):
        return self.rows != self.original

    def save(self):
        rows = self.validator(self.rows)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name('.' + self.path.name + '.lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError('다른 창에서 저장 중입니다. 잠시 후 다시 시도하세요.') from exc
        os.close(fd)
        temporary = None
        try:
            if self.digest() != self.version:
                raise ValueError('다른 창에서 저장 파일을 변경했습니다. 다시 열어 주세요.')
            content = json.dumps({'version': 1, 'rows': rows}, ensure_ascii=False, indent=2).encode('utf-8')
            fd, name = tempfile.mkstemp(prefix='.mileage-', suffix='.tmp', dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(fd, 'wb') as output:
                output.write(content); output.flush(); os.fsync(output.fileno())
            os.replace(temporary, self.path)
            self.rows, self.original = rows, deepcopy(rows)
            self.version = sha256(content).hexdigest()
        finally:
            if temporary: temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)


def vehicles_store():
    return LocalRows(paths.at('mileage-vehicles.json'), validate_vehicles)


class MileageBook(LocalRows):
    def __init__(self, folder):
        self.folder = Path(folder)
        super().__init__(self.folder / BOOK_NAME, self.validate_rows)
        self.undo_stack, self.redo_stack = [], []

    def validate_rows(self, rows):
        result, seen = [], set()
        for row in rows:
            item = checked_row(row)
            if item['id'] in seen: raise ValueError('마일리지 내부 식별자가 중복됩니다.')
            seen.add(item['id']); result.append(item)
        return result

    def commit_rows(self, rows):
        checked = self.validate_rows(rows)
        if checked != self.rows:
            self.undo_stack.append(deepcopy(self.rows)); self.redo_stack.clear()
            self.rows = checked

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(deepcopy(self.rows)); self.rows = self.undo_stack.pop()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(deepcopy(self.rows)); self.rows = self.redo_stack.pop()

    def image_path(self, row):
        root = (self.folder / 'mileage-maps').resolve()
        path = (self.folder / text(row.get('map'))).resolve()
        if path.parent != root or path.suffix.lower() not in IMAGE_TYPES.values():
            raise ValueError('지도 이미지는 작업 폴더의 mileage-maps 안에 있어야 합니다.')
        return path

    def check_image(self, row):
        path = self.image_path(row)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != row.get('map_sha256'):
            raise ValueError('지도 이미지가 없거나 변경되었습니다. 다시 첨부하세요.')
        return path

    def attach_image(self, filename):
        from PIL import Image
        source = Path(filename)
        if not source.is_file() or source.stat().st_size > MAX_IMAGE:
            raise ValueError('20MB 이하의 지도 이미지 파일을 선택하세요.')
        content = source.read_bytes()
        from io import BytesIO
        with Image.open(BytesIO(content)) as image:
            suffix = IMAGE_TYPES.get(image.format)
            if suffix is None: raise ValueError('지도 이미지는 PNG 또는 JPG 형식이어야 합니다.')
            image.verify()
        digest = sha256(content).hexdigest()
        directory = self.folder / 'mileage-maps'
        directory.mkdir(parents=True, exist_ok=True)
        # No map can be mistaken for a card receipt in the folder's top-level scan.
        target = directory / (digest + suffix)
        if target.exists():
            if target.read_bytes() != content: raise ValueError('기존 지도 이미지가 변경되었습니다.')
        else:
            fd, name = tempfile.mkstemp(prefix='.map-', dir=directory)
            temp = Path(name)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(content); output.flush(); os.fsync(output.fileno())
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        return {'map': target.relative_to(self.folder).as_posix(), 'map_sha256': digest, 'map_name': source.name}

    def save(self):
        for row in self.rows: self.check_image(row)
        super().save()


def new_row():
    return {'id': uuid4().hex, 'passengers': '0'}
