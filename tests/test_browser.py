"""브라우저를 못 열었을 때 무엇을 알려주는가.

'없다'와 '떴다가 바로 닫혔다'는 다른 문제다. 둘을 같은 말로 안내했더니
(“Edge나 Chrome이 깔려 있어야 합니다”) 멀쩡히 깔린 브라우저를 찾으러
다니게 만들었다. 실측 2026-08, 회사 PC.
"""

from src import browser


def test_떴다가_닫혔으면_그렇게_말한다():
    말 = browser._why([
        "msedge: BrowserType.launch_persistent_context: Target page, context or browser has been closed",
        "chrome: BrowserType.launch_persistent_context: Target page, context or browser has been closed",
        "chromium: BrowserType.launch_persistent_context: Target page, context or browser has been closed",
    ])
    assert "곧바로 닫혔습니다" in 말
    assert "깔려 있어야 합니다" not in 말  # 엉뚱한 데를 보게 만들면 안 된다
    assert "가로채는" in 말


def test_없으면_설치를_안내한다():
    말 = browser._why([
        "msedge: BrowserType.launch: Executable doesn't exist at C:\\...\\msedge.exe",
        "chrome: BrowserType.launch: Executable doesn't exist",
        "chromium: BrowserType.launch: Executable doesn't exist",
    ])
    assert "깔려 있어야 합니다" in 말


def test_하나라도_다른_이유면_섞어_말하지_않는다():
    """전부 '닫힘'일 때만 그 진단을 내린다. 하나라도 다르면 근거가 약하다."""
    말 = browser._why([
        "msedge: Target page, context or browser has been closed",
        "chrome: Executable doesn't exist",
        "chromium: Executable doesn't exist",
    ])
    assert "깔려 있어야 합니다" in 말


def test_시도한_것을_그대로_남긴다():
    말 = browser._why(["msedge: 어쩌구", "chrome: 저쩌구"])
    assert "msedge: 어쩌구" in 말 and "chrome: 저쩌구" in 말
