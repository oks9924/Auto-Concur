"""문자열 검색·교체 계획. 원본 거래 열과 범위 밖 행은 변경하지 않는다."""
from copy import deepcopy
import re

from .sheet import EDITABLE


def matches(data, columns, rows, find, field=None, case_sensitive=False, whole=False):
    if not find:
        raise ValueError('찾을 내용을 입력해 주세요.')
    if field is not None and field not in EDITABLE:
        raise ValueError('입력 가능한 열에서만 바꿀 수 있습니다.')
    pattern = re.compile(('^' if whole else '') + re.escape(find) + ('$' if whole else ''),
                         0 if case_sensitive else re.IGNORECASE)
    found = []
    for r in rows:
        for c, name in enumerate(columns):
            if name not in EDITABLE or (field and name != field):
                continue
            value = str(data[r][c])
            if pattern.search(value):
                found.append((r, c))
    return pattern, found


def replaced(data, pattern, cells, replacement):
    result = deepcopy(data)
    for r, c in cells:
        result[r][c] = pattern.sub(lambda match: replacement, str(data[r][c]))
    return result
