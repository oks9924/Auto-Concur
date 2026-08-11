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
import time

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


BLANK = ("", "about:blank", "chrome://newtab/", "edge://newtab/")


def first_page(ctx, wait_ms: int = 5000):
    """쓸 창 하나를 고른다. 남는 빈 창은 닫는다.

    브라우저가 자기 시작 페이지를 그리는 데 잠깐 걸린다. 그 전에 물으면
    창이 없어 보여서 우리가 하나를 더 만들고, 그러면 빈 창이 우리 창 위를
    덮는다. 사람 눈에는 '빈 화면이 떴다가, 닫으니까 그제서야 현대카드가
    뜨는' 것으로 보인다 (실측 2026-08).

    그래서 먼저 기다려 보고, 그래도 없을 때만 만든다. 이미 열려 있는 빈 창은
    닫는다 - 내용이 있는 창은 사람이 보던 것일 수 있으니 두고 간다.
    """
    for _ in range(max(1, wait_ms // 100)):
        if ctx.pages:
            break
        time.sleep(0.1)

    pages = list(ctx.pages) or [ctx.new_page()]
    page = pages[0]
    for extra in pages[1:]:
        if extra.url in BLANK:
            extra.close()
    page.bring_to_front()
    return page


def open_first(ctx, url: str, wait_until: str = "domcontentloaded"):
    """쓸 창을 고르고 주소로 이동한다. 뒤늦게 뜨는 빈 창까지 치운다.

    브라우저가 자기 시작 탭을 우리가 창을 고른 뒤에 여는 경우가 있다. 그러면
    first_page 로 하나를 골라 이동시켜 놓아도, 그 빈 탭이 나중에 우리 창 위를
    덮는다. 사람 눈에는 '빈 창을 닫아야 진짜 창이 뜨는' 것으로 보인다.

    치우는 것은 첫 이동 직후 한 번뿐이다. 그 뒤에 열리는 빈 창은 전표 인쇄
    팝업일 수 있고, 그건 우리가 쓰는 창이다 - 닫으면 안 된다.
    """
    page = first_page(ctx)
    page.goto(url, wait_until=wait_until)

    # 한 번만 치우면 놓친다. 시작 탭이 이동보다 늦게 뜨는 경우가 있어서
    # 잠시 동안 지켜본다. 사람이 로그인하는 데는 이보다 훨씬 오래 걸리므로
    # 이 시간 안에 열리는 빈 창은 우리가 쓸 창이 아니다.
    closed = 0
    for _ in range(12):
        time.sleep(0.5)
        for other in list(ctx.pages):
            if other is not page and other.url in BLANK:
                other.close()
                closed += 1

    # 그래도 빈 창이 남는다고 하면 우리가 못 보는 창이라는 뜻이다. 몇 개가
    # 보이고 무엇을 닫았는지 남겨야 그 다음을 판단할 수 있다.
    others = [p.url for p in ctx.pages if p is not page]
    print(f"  (브라우저 창 {len(ctx.pages)}개"
          + (f", 빈 창 {closed}개를 닫았습니다" if closed else "")
          + (f", 남은 창: {others}" if others else "")
          + ")")
    page.bring_to_front()
    return page
