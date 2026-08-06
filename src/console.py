"""콘솔 한글 출력 보정.

Windows에서 파이썬은 콘솔 코드페이지로 stdout을 인코딩한다. 한국어 Windows는
cp949라 한글이 나오지만, 영문 Windows(cp1252)에서는 한글을 찍는 순간
UnicodeEncodeError로 죽는다. 이름 변경 도중에 죽으면 어디까지 바뀌었는지
알기 어려우므로 진입 시점에 UTF-8로 맞춰둔다.
"""

from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
