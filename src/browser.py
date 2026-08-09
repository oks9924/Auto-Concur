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
    raise RuntimeError(
        "브라우저를 열지 못했습니다. Edge나 Chrome이 깔려 있어야 합니다.\n  "
        + "\n  ".join(tried)
    )
