"""콘솔 입출력 보정.

출력: Windows에서 파이썬은 콘솔 코드페이지로 stdout을 인코딩한다. 한국어
Windows는 cp949라 한글이 나오지만, 영문 Windows(cp1252)에서는 한글을 찍는 순간
UnicodeEncodeError로 죽는다. 이름 변경 도중에 죽으면 어디까지 바뀌었는지
알기 어려우므로 진입 시점에 UTF-8로 맞춰둔다.

입력: 브라우저 작업을 기다리는 동안 사람이 다음 명령을 쳐 넣으면 그 텍스트가
Enter로 소비돼서 스크립트가 그냥 진행해버린다. 그래서 wait_enter를 쓴다.
"""

from __future__ import annotations

import sys


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _drain() -> None:
    """프롬프트가 뜨기 전에 눌린 키를 버린다."""
    try:
        import msvcrt  # Windows
    except ImportError:
        try:
            import termios

            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except Exception:
            pass
        return
    while msvcrt.kbhit():
        msvcrt.getwch()


def open_folder(path) -> None:
    """작업이 끝난 폴더를 탐색기로 연다. 안 열려도 그냥 넘어간다."""
    import subprocess
    import sys as _sys

    try:
        if _sys.platform == "win32":
            import os

            os.startfile(path)  # noqa: S606 - Windows 전용
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass  # 폴더가 안 열리는 것은 작업 결과와 상관없다


def wait_enter(message: str) -> None:
    """빈 줄이 올 때까지 기다린다.

    앞 단계가 끝난 줄 알고 다음 명령을 쳐 넣는 일이 실제로 있었다. 그냥
    input()이면 그 명령이 Enter로 먹혀서 카드 인증 전에 조회로 넘어가버린다.
    빈 줄만 통과시키고, 뭔가 입력하면 무시했다고 알려준다.
    """
    _drain()
    while input(message).strip():
        print("  (그냥 Enter만 눌러라. 방금 입력한 건 실행되지 않았다.)")
