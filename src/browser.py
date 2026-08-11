"""브라우저를 연다. PC에 이미 깔려 있는 것을 먼저 쓴다.

Playwright는 기본으로 전용 Chromium을 따로 내려받는다. 회사 방화벽이
cdn.playwright.dev 를 막으면 그 설치가 안 된다(실측: connect EACCES ...:443).
Windows에는 Edge가 늘 깔려 있으니 그것을 쓰면 내려받을 것이 없다.

순서는 Edge -> Chrome -> 내려받은 Chromium 이다. 순서를 바꾸면 브라우저
프로필(로그인 상태)이 다른 브라우저 것으로 열려서 다시 로그인해야 한다.
특정 브라우저를 쓰고 싶으면 CONCUR_BROWSER 환경변수에 msedge / chrome /
chromium 중 하나를 적는다.
"""

from __future__ import annotations

import os

# None은 Playwright가 내려받은 Chromium이다.
CHANNELS = ["msedge", "chrome", None]


def channels() -> list[str | None]:
    """시도할 순서. 환경변수로 하나만 고를 수 있다."""
    want = (os.environ.get("CONCUR_BROWSER") or "").strip().lower()
    if not want:
        return CHANNELS
    return [None] if want == "chromium" else [want]


def launch(pw, profile_dir, only: str | None = None, **kwargs):
    """launch_persistent_context 를 브라우저를 바꿔가며 시도한다.

    only를 주면 그것만 쓴다 (inspect_page의 --channel).
    """
    order = channels() if not only else [None if only == "chromium" else only]
    tried = []
    for channel in order:
        try:
            return pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                **({"channel": channel} if channel else {}),
                **kwargs,
            )
        except Exception as exc:  # 없는 브라우저면 다음 것으로 넘어간다
            tried.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0]}")
    raise RuntimeError(_why(tried))


# 브라우저가 '없는' 것과 '떴다가 바로 닫힌' 것은 다른 문제다. 둘을 같은 말로
# 안내했더니(“Edge나 Chrome이 깔려 있어야 합니다”) 있는 브라우저를 찾으러
# 다니게 만들었다. 실측: 셋 다 'has been closed' 였고, 원인은 브라우저 부재가
# 아니라 브라우저 실행을 가로채는 프로그램이었다.
CLOSED = "has been closed"

CLOSED_HELP = (
    "브라우저가 떴다가 곧바로 닫혔습니다. 브라우저는 깔려 있습니다.\n"
    "  브라우저 실행을 가로채는 프로그램(격리 브라우저·보안 솔루션)이 있으면\n"
    "  이렇게 됩니다. 그런 PC에서는 자동화가 붙을 수 없습니다.\n"
    "  Edge 주소창에 edge://policy 를 치고 RemoteDebuggingAllowed 도 확인해 주세요."
)

MISSING_HELP = "브라우저를 찾지 못했습니다. Edge나 Chrome이 깔려 있어야 합니다."


def _why(tried: list[str]) -> str:
    head = CLOSED_HELP if all(CLOSED in x for x in tried) else MISSING_HELP
    return head + "\n  " + "\n  ".join(tried)
