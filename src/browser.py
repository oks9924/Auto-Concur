"""브라우저를 연다. 내려받은 Chromium을 먼저 쓰고, 없으면 PC의 브라우저를 쓴다.

순서는 내려받은 Chromium -> Edge -> Chrome 이다.

Chromium이 앞인 이유: Playwright가 자기 버전에 맞춰 내려받은 것이라 가장
얌전하다. 한동안 Edge를 앞에 뒀는데, Edge는 자기 시작 탭을 따로 띄워서 빈
창이 우리 창을 덮었다(실측 2026-08). 그 문제가 없던 쪽으로 되돌린다.

Edge가 뒤에 남아 있는 이유: 회사 방화벽이 cdn.playwright.dev 를 막으면
Chromium을 아예 못 받는다(실측: connect EACCES ...:443). 그런 PC에서는
Windows에 늘 있는 Edge로 넘어간다.

특정 브라우저를 쓰고 싶으면 CONCUR_BROWSER 환경변수에 chromium / msedge /
chrome 중 하나를 적는다.

브라우저를 바꾸면 프로필(로그인 상태)이 그 브라우저 것으로 새로 시작한다.
바꾼 뒤 브라우저가 뜨자마자 닫히면 browser-profile 폴더를 지우고 다시
해본다 - 새 브라우저가 만든 프로필을 옛 브라우저가 못 여는 경우가 있다.
"""

from __future__ import annotations

import os
import time

# None은 Playwright가 내려받은 Chromium이다.
CHANNELS = [None, "msedge", "chrome"]


def channels() -> list[str | None]:
    """시도할 순서. 환경변수로 하나만 고를 수 있다."""
    want = (os.environ.get("CONCUR_BROWSER") or "").strip().lower()
    if not want:
        return CHANNELS
    return [None] if want == "chromium" else [want]


MISSING = "Executable doesn't exist"


def install_chromium() -> str | None:
    """Chromium을 지금 내려받는다. 받았으면 None, 못 받았으면 이유를 준다.

    exe로 묶을 때 브라우저는 안 들어간다 - 넣으면 파일이 200MB를 넘고, 실행할
    때마다 그만큼을 임시 폴더에 푼다. 대신 처음 한 번 받는다. 받은 것은
    사용자 폴더에 남으므로 그 다음부터는 그냥 열린다.

    setup.bat 을 거친 PC에는 이미 있어서 이 함수까지 오지 않는다. exe만 받은
    사람의 첫 실행에서만 도는 길이다.
    """
    import subprocess

    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
    except Exception as exc:  # playwright 구조가 바뀌면 여기서 걸린다
        return f"설치 도구를 찾지 못했습니다: {exc}"

    print("  브라우저를 내려받습니다. 처음 한 번만 하고 몇 분 걸립니다...")
    try:
        done = subprocess.run(
            [*compute_driver_executable(), "install", "chromium"],
            env=get_driver_env(),
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return str(exc)
    if done.returncode != 0:
        lines = (done.stderr or done.stdout or "").strip().splitlines()
        return lines[-1] if lines else "내려받지 못했습니다"
    print("  브라우저를 받았습니다.")
    return None


def launch(pw, profile_dir, only: str | None = None, **kwargs):
    """launch_persistent_context 를 브라우저를 바꿔가며 시도한다.

    only를 주면 그것만 쓴다 (inspect_page의 --channel).
    """
    order = channels() if not only else [None if only == "chromium" else only]
    tried, installed = [], False

    def start(channel):
        return pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            **({"channel": channel} if channel else {}),
            **kwargs,
        )

    for channel in order:
        try:
            return start(channel)
        except Exception as exc:  # 없는 브라우저면 다음 것으로 넘어간다
            first = str(exc).splitlines()[0]
            # Chromium이 아직 없으면 지금 받고 한 번 더 해본다. exe만 받은
            # 사람의 첫 실행이 여기다.
            if channel is None and MISSING in str(exc) and not installed:
                installed = True
                why = install_chromium()
                if why is None:
                    try:
                        return start(channel)
                    except Exception as retry:
                        first = str(retry).splitlines()[0]
                else:
                    first = f"{first} (내려받기도 실패: {why})"
            tried.append(f"{channel or 'chromium'}: {first}")
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
