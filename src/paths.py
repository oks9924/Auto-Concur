"""프로그램이 쓰는 폴더의 기준점.

지금까지는 현재 폴더 기준이었다. run.bat이 `cd /d "%~dp0"` 로 옮겨준 덕에
맞아떨어졌을 뿐이다. exe로 묶어 남이 실행하면(DuoNX 같은 것은 실행 파일만
등록할 수 있고 시작 위치를 못 준다) 현재 폴더가 어디일지 알 수 없다.
`settings.json` 이 엉뚱한 시스템 폴더에 생기는 식이 된다.

그래서 기준을 파일 위치로 고정한다.
  - exe로 묶였으면: exe가 있는 폴더
  - 그냥 파이썬으로 돌리면: 저장소 폴더 (src의 부모)
"""

from __future__ import annotations

import sys
from pathlib import Path


def base() -> Path:
    """설정·전표·브라우저 프로필이 놓일 폴더."""
    if getattr(sys, "frozen", False):
        # PyInstaller. sys.executable 은 exe 자신이다. _MEIPASS(임시 풀림
        # 폴더)를 쓰면 안 된다 - 프로그램이 끝나면 지워진다.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def at(*parts: str) -> Path:
    return base().joinpath(*parts)


def folder(value) -> Path:
    """설정에 적힌 폴더. 상대경로면 프로그램 폴더 기준으로 읽는다.

    '찾아보기'로 고른 값은 절대경로라 그대로 쓴다. 기본값 'downloads' 처럼
    상대경로일 때만 기준을 붙인다.
    """
    path = Path(value)
    return path if path.is_absolute() else base() / path


def stamp() -> str:
    """지금 돌고 있는 코드가 언제 것인지.

    'pull 했는데 왜 그대로냐', '빌드를 다시 해야 하냐' 를 눈으로 가리려고 둔다.
    exe면 exe 파일의 시각, 소스면 src 안에서 가장 최근에 고친 파일의 시각이다.
    """
    import sys
    from datetime import datetime

    if getattr(sys, "frozen", False):
        newest = Path(sys.executable).stat().st_mtime
    else:
        files = list((Path(__file__).resolve().parent).glob("*.py"))
        newest = max(f.stat().st_mtime for f in files)
    return datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
