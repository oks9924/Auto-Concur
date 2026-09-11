"""프로그램 작업 데이터. 엑셀은 선택적 입출력 형식이며 실행에는 필요 없다."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from . import sheet, settings

NATIVE_NAME = 'workbook.json'


def fingerprint(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.workbook-', suffix='.json', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def normalize(row: dict, cfg: dict | None = None) -> dict:
    result = {key: str(value if value is not None else '').strip() for key, value in row.items()}
    year = int(str(result['거래일'])[:4])
    for key in sheet.DATE_COLUMNS:
        if result.get(key):
            parsed = sheet._as_date(result[key], year)
            if parsed is None:
                raise sheet.SheetError(f'{key}: 날짜를 읽을 수 없습니다. 숙박 날짜 달력을 사용하거나 2026-08-17, 20260817, 8/17처럼 입력해 주세요.')
            result[key] = parsed.isoformat()
    start, end = (result.get(key, '') for key in sheet.DATE_COLUMNS)
    if bool(start) != bool(end):
        raise sheet.SheetError('입실·퇴실은 함께 입력하거나 모두 비워 주세요.')
    if start and end <= start:
        raise sheet.SheetError('퇴실날짜는 입실날짜보다 뒤여야 합니다. 연도를 넘기는 숙박이면 퇴실 연도도 지정해 주세요.')
    if cfg is not None:
        for key, choices in settings.choices(cfg).items():
            if result.get(key) and result[key] not in choices:
                raise sheet.SheetError(f'{key}: 목록에 없는 값입니다: {result[key]}')
    return result


class Worksheet:
    def __init__(self, source: Path, target: Path | None = None):
        self.source = source
        self.target = target or source.parent / NATIVE_NAME
        self.version = fingerprint(self.target)
        # 날짜 입력 오류는 편집창에서 고칠 수 있어야 한다. 저장/C단계에서는 검증한다.
        sheet.load(source, validate_lodging=False)
        self.rows = sheet.read_raw(source)
        self.columns = list(dict.fromkeys([*self.rows[0], *sheet.EDITABLE]))
        self.original = deepcopy(self.rows)
        self.dirty = False

    @property
    def draft_path(self):
        return self.target.with_name('workbook.draft.json')

    @property
    def backup_path(self):
        return self.target.with_name('workbook.previous.json')

    def update(self, index: int, values: dict[str, str]) -> None:
        row = dict(self.rows[index])
        row.update({key: str(value).strip() for key, value in values.items() if key in sheet.EDITABLE})
        self.rows[index] = normalize(row)
        self.dirty = self.rows != self.original

    def replace_edits(self, rows: list[dict]) -> None:
        if len(rows) != len(self.rows):
            raise sheet.SheetError('전표 행 개수는 편집으로 바꿀 수 없습니다.')
        self.rows = [{**original, **{key: str(edited.get(key, '')).strip() for key in sheet.EDITABLE}}
                     for original, edited in zip(self.rows, rows)]
        self.dirty = self.rows != self.original

    def save_draft(self):
        write_json(self.draft_path, {'version': 1, 'base': self.version, 'rows': self.rows})

    def recover_draft(self) -> bool:
        if not self.draft_path.exists():
            return False
        payload = json.loads(self.draft_path.read_text(encoding='utf-8'))
        if payload.get('base') != self.version:
            raise sheet.SheetError('임시 저장 이후 원본이 변경되었습니다. 임시본을 자동 적용하지 않았습니다.')
        rows = sheet.read_raw(self.draft_path)
        keys = ('파일명', '승인번호', '거래일', '금액')
        if len(rows) != len(self.rows) or any(any(a.get(k, '') != b.get(k, '') for k in keys)
                                             for a, b in zip(self.rows, rows)):
            raise sheet.SheetError('임시본의 전표 정보가 달라 복원하지 않았습니다.')
        self.replace_edits(rows)
        return self.dirty

    def restore_previous(self) -> None:
        rows = sheet.read_raw(self.backup_path)
        key = lambda row: (row.get('승인번호'), row.get('파일명'), row.get('거래일'), row.get('금액'))
        saved = {key(row): row for row in rows}
        self.replace_edits([saved.get(key(row), row) for row in self.rows])

    def save(self, cfg: dict) -> None:
        if fingerprint(self.target) != self.version:
            raise sheet.SheetError('편집 중 저장 데이터가 변경됐습니다. 다시 불러온 뒤 편집해 주세요.')
        normalized = []
        for number, row in enumerate(self.rows, 1):
            try:
                normalized.append(normalize(row, cfg))
            except (sheet.SheetError, ValueError) as exc:
                raise sheet.SheetError(f'{number}행: {exc}') from exc
        if self.target.exists():
            previous = json.loads(self.target.read_text(encoding='utf-8'))
            write_json(self.backup_path, previous)
        if fingerprint(self.target) != self.version:
            raise sheet.SheetError('저장 중 원본이 변경되어 덮어쓰지 않았습니다.')
        write_json(self.target, {'version': 1, 'rows': normalized})
        self.rows = normalized
        self.original = deepcopy(normalized)
        self.version = fingerprint(self.target)
        self.source = self.target
        self.dirty = False
        self.draft_path.unlink(missing_ok=True)
