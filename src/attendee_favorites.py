"""Local attendee shortcuts. Never contacts Concur or rewrites worksheet rows."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata
from . import paths


def split_people(value: str) -> list[str]:
    """Keep the established comma-separated search-value format and order."""
    result, seen = [], set()
    for item in str(value or '').split(','):
        item = item.strip()
        if item and item.casefold() not in seen:
            result.append(item)
            seen.add(item.casefold())
    return result


def validate_person(label: str, value: str) -> dict[str, str]:
    label, value = label.strip(), value.strip()
    if not value:
        raise ValueError('Concur에서 사용할 참석자 검색값을 입력하세요.')
    if len(label) > 120 or len(value) > 200:
        raise ValueError('표시 이름은 120자, 검색값은 200자 이내로 입력하세요.')
    if any(unicodedata.category(c).startswith('C') for c in label + value):
        raise ValueError('줄바꿈이나 제어 문자는 등록할 수 없습니다.')
    if any(c in value for c in ',;，；'):
        raise ValueError('한 항목에는 한 명의 검색값만 등록하세요. 여러 명은 각각 추가하세요.')
    return {'label': label or value, 'value': value}


def validate_people(people) -> list[dict[str, str]]:
    if not isinstance(people, list):
        raise ValueError('참석자 목록 형식이 올바르지 않습니다.')
    result, seen = [], set()
    for person in people:
        if not isinstance(person, dict) or not all(isinstance(person.get(k), str) for k in ('label', 'value')):
            raise ValueError('참석자 항목 형식이 올바르지 않습니다.')
        item = validate_person(person['label'], person['value'])
        key = item['value'].casefold()
        if key in seen:
            raise ValueError('같은 검색값이 중복되어 있습니다. 기존 항목을 수정하세요.')
        seen.add(key)
        result.append(item)
    return result


class FavoriteStore:
    """Atomic save plus stale-editor and simultaneous-write protection."""
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else paths.at('attendee-favorites.json')
        raw = self.path.read_bytes() if self.path.exists() else None
        self.version = self._digest(raw)
        if raw is None:
            self.people = []
        else:
            payload = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(payload, dict) or payload.get('version') != 1:
                raise ValueError('지원하지 않는 참석자 목록 형식입니다. 원본 파일은 변경하지 않았습니다.')
            self.people = validate_people(payload.get('people'))

    @staticmethod
    def _digest(raw):
        return hashlib.sha256(raw).hexdigest() if raw is not None else None

    def save(self, people):
        clean = validate_people(people)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name('.attendee-favorites.lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise ValueError('다른 창에서 목록을 저장 중입니다. 잠시 후 다시 시도하세요.') from exc
        os.close(fd)
        temp = None
        try:
            current = self.path.read_bytes() if self.path.exists() else None
            if self._digest(current) != self.version:
                raise ValueError('다른 창에서 목록을 변경했습니다. 이 창을 닫고 다시 열어 주세요.')
            raw = json.dumps({'version': 1, 'people': clean}, ensure_ascii=False, indent=2).encode('utf-8')
            fd, name = tempfile.mkstemp(prefix='.attendee-favorites-', suffix='.tmp', dir=self.path.parent)
            temp = Path(name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            self.people, self.version = clean, self._digest(raw)
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
