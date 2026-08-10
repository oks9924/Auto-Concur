"""회사 PC에서 파일이 잠깐 잠기는 것을 넘긴다.

실측(2026-08, 회사망 PC): 파이썬이 파일을 여는 순간 PermissionError [Errno 13]
가 났다. 파일 권한은 멀쩡했고(icacls: Authenticated Users 수정 가능), 같은
파일을 잠시 뒤에 열면 그냥 열렸다. 걸리는 파일도 그때그때 달랐다 -
처음에는 src/download_slips.py, 다음에는 settings.json.

한 파일의 문제가 아니라 '그 순간 그 파일이 잠겨 있었다'는 문제다. 실시간
검사가 파일을 검사하는 동안 물고 있으면 이렇게 된다. 그래서 파일마다 따로
막지 않고, 파일을 여는 동작 자체를 몇 번 다시 해본다.

계속 실패하면 삼키지 않고 멈춘다 - 못 읽은 것을 읽은 척하면 안 된다.
"""

from __future__ import annotations

import time

# 다시 해보기까지 기다릴 시간(초). 검사는 보통 1초 안에 끝난다.
WAITS = (0.5, 1.5, 3.0)

LOCKED_HELP = (
    "  회사 백신이 이 폴더를 검사하며 파일을 잠그고 있을 수 있습니다.\n"
    "  다시 눌러 보시고, 계속 그러면 IT에 이 폴더의 검사 예외를 요청해 주세요."
)


def _locked(exc, what: str) -> str:
    """실제로 잠긴 파일 이름. 없으면 부르는 쪽이 준 이름을 쓴다.

    exe로 묶으면 우리가 부른 이름과 실제로 못 연 파일이 다르다. 모듈 하나를
    불러오는 데 딸린 DLL 수십 개가 열리기 때문이다. 우리가 부른 이름만
    찍었더니 'src/download_slips.py 가 잠겼다'고 나왔는데, exe 안에는 그런
    파일이 있지도 않았다. 엉뚱한 곳을 보게 만든다.
    """
    return str(getattr(exc, "filename", None) or what)


def keep_trying(what: str, fn, waits=None):
    """fn()을 부른다. 파일이 잠겨 있으면 잠깐 뒤에 다시 해본다.

    what은 못 열었을 때 보여줄 이름이다 ('settings.json' 같은 것). 예외가
    진짜 파일 이름을 갖고 있으면 그쪽을 쓴다.
    """
    last = None
    for wait in (0.0, *(WAITS if waits is None else waits)):
        if wait:
            time.sleep(wait)
        try:
            return fn()
        except PermissionError as exc:  # 잠겨 있다. 다시 해본다
            last = exc
            print(f"  ({_locked(exc, what)} 이(가) 잠겨 있어 다시 시도합니다)")
    raise PermissionError(
        f"{_locked(last, what)} 을(를) 열지 못했습니다: {last}\n{LOCKED_HELP}"
    ) from last
