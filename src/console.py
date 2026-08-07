"""콘솔 입출력 보정.

출력: Windows에서 파이썬은 콘솔 코드페이지로 stdout을 인코딩한다. 한국어
Windows는 cp949라 한글이 나오지만, 영문 Windows(cp1252)에서는 한글을 찍는 순간
UnicodeEncodeError로 죽는다. 이름 변경 도중에 죽으면 어디까지 바뀌었는지
알기 어려우므로 진입 시점에 UTF-8로 맞춰둔다.

입력: 브라우저 작업을 기다리는 동안 사람이 다음 명령을 쳐 넣으면 그 텍스트가
Enter로 소비돼서 스크립트가 그냥 진행해버린다. 그래서 wait_enter를 쓴다.
"""

from __future__ import annotations

import atexit
import os
import sys

# 창에서 띄운 단계는 끝나도 콘솔을 바로 닫지 않는다. Windows에서 새 콘솔로 띄우면
# 프로세스가 끝나는 순간 창이 사라져서 마지막 안내나 오류를 읽을 수 없다.
# (실제로 "리포트 열고 Enter 눌렀더니 그냥 꺼졌다"는 일이 있었다.)
HOLD_ENV = "AUTO_CONCUR_HOLD"

# 창(GUI)에서 돌릴 때는 콘솔이 없다. 창이 자기 방식으로 물어보게 갈아끼운다.
_ask = None


def set_prompt(fn) -> None:
    """'Enter를 눌러 주세요'를 어떻게 물을지 정한다. None이면 콘솔 입력."""
    global _ask
    _ask = fn


def _hold() -> None:
    try:
        input("\n창을 닫으려면 Enter를 눌러 주세요 > ")
    except (EOFError, KeyboardInterrupt):
        pass


def setup() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    if os.environ.get(HOLD_ENV):
        atexit.register(_hold)


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
    """사람이 브라우저에서 할 일을 마칠 때까지 기다린다.

    message는 '카드 인증을 끝내셨으면' 처럼 끝나는 말이다. 무엇을 눌러야
    하는지는 여기서 붙인다 - 콘솔이면 Enter, 창이면 확인 버튼이라서
    부르는 쪽이 알 수 없다.

    콘솔에서는 빈 줄만 통과시킨다. 앞 단계가 끝난 줄 알고 다음 명령을 쳐
    넣는 일이 실제로 있었는데, 그냥 input()이면 그 명령이 Enter로 먹혀서
    카드 인증 전에 조회로 넘어가버린다.
    """
    if _ask is not None:
        _ask(f"{message} 아래 확인 버튼을 눌러 주세요.")
        return
    _drain()
    while input(f"{message} Enter를 눌러 주세요 > ").strip():
        print("  (그냥 Enter만 눌러 주세요. 방금 입력하신 내용은 실행되지 않았습니다.)")
