"""배치 파일은 ASCII만 담는다.

cmd는 이 파일을 그때그때의 콘솔 코드페이지(949, 65001, 437...)로 읽는다.
ASCII가 아닌 바이트가 하나라도 있으면 코드페이지가 다른 PC에서 잘못 해석되고,
파서가 토큰 중간에서 줄을 끊어버린다. 실제로 CP949도 UTF-8도 깨졌다.
한글 안내는 README.md 에 둔다.
"""

from pathlib import Path

import pytest

BATS = sorted(Path(__file__).resolve().parent.parent.glob("*.bat"))


def test_배치_파일이_있다():
    assert {p.name for p in BATS} == {"setup.bat", "run.bat"}


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_ascii만_들어_있다(path):
    data = path.read_bytes()
    나쁜바이트 = [(i, data[i]) for i in range(len(data)) if data[i] > 127]
    assert not 나쁜바이트, f"{path.name}: ASCII가 아닌 바이트 {나쁜바이트[:5]}"


@pytest.mark.parametrize("path", BATS, ids=lambda p: p.name)
def test_줄바꿈이_crlf다(path):
    # LF만 있으면 goto 라벨을 못 찾는 cmd가 있다.
    data = path.read_bytes()
    assert data.count(b"\n") == data.count(b"\r\n")


def test_브라우저는_이미_깔린_것부터_쓴다():
    """회사 방화벽이 cdn.playwright.dev 를 막으면 번들 Chromium을 못 받는다.

    Windows에는 Edge가 늘 있으니 그것부터 쓴다. 순서를 바꾸면 브라우저
    프로필(로그인 상태)이 다른 브라우저 것으로 열려 다시 로그인해야 한다.
    """
    from src import browser

    assert browser.channels() == ["msedge", "chrome", None]


def test_환경변수로_브라우저를_고를_수_있다(monkeypatch):
    from src import browser

    monkeypatch.setenv("CONCUR_BROWSER", "chrome")
    assert browser.channels() == ["chrome"]
    monkeypatch.setenv("CONCUR_BROWSER", "chromium")
    assert browser.channels() == [None]


def test_설치가_브라우저_다운로드_실패로_멈추지_않는다():
    """Edge를 쓰므로 번들 Chromium은 없어도 된다."""
    from pathlib import Path

    text = Path("setup.bat").read_text(encoding="ascii")
    install = text.split("playwright install chromium")[1].splitlines()[1]
    assert "goto nobrowser" in install  # goto fail 이면 안 된다
    assert ":nobrowser" in text


def test_실행_기록을_남기고_오류면_멈춘다():
    """창이 그냥 닫히면 무엇이 잘못됐는지 알 방법이 없다."""
    from pathlib import Path

    text = Path("run.bat").read_text(encoding="ascii")
    assert "> run-log.txt 2>&1" in text  # 늘 파일로 남긴다
    assert "type run-log.txt" in text  # 죽으면 화면에 보여주고
    assert "pause" in text.split(":crashed")[1]  # 창을 붙잡는다

