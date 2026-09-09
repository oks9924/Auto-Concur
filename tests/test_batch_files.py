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
    assert {p.name for p in BATS} == {"setup.bat", "run.bat", "build_exe.bat", "make_exe.bat"}


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


def test_내려받은_Chromium을_먼저_쓴다():
    """Edge를 앞에 뒀더니 자기 시작 탭을 띄워 우리 창을 덮었다(실측 2026-08).

    Chromium은 Playwright가 자기 버전에 맞춰 받은 것이라 가장 얌전하다.
    Edge는 뒤에 남긴다 - 회사망에서 Chromium을 아예 못 받는 PC가 있다.
    """
    from src import browser

    assert browser.channels() == [None, "msedge", "chrome"]


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



def test_winget는_스토어를_뒤지지_않는다():
    """실측 2026-09-09: winget이 msstore 까지 뒤지다 죽었다.

      Failed when searching source: winget
      0x8a15000f : Data required by the source is missing

    msstore 는 지역 정보 동의를 먼저 요구한다. 우리가 받을 패키지는 거기
    있지도 않은데 그것 때문에 설치 전체가 죽는다.
    """
    text = (BATS[0].parent / "setup.bat").read_text(encoding="ascii")
    winget = [x for x in text.splitlines() if x.startswith("winget install")]
    assert len(winget) == 1
    assert "--source winget" in winget[0]


def test_winget가_실패하면_python_org로_간다():
    """실측: 실패했는데도 '설치됐으니 창을 다시 여세요' 라고 안내했다.

    창을 다시 열어도 없는 파이썬은 안 생긴다. 실패는 실패로 보고 다음
    방법으로 넘어가야 한다.
    """
    text = (BATS[0].parent / "setup.bat").read_text(encoding="ascii")
    줄 = [x.strip() for x in text.splitlines()]
    설치 = next(i for i, x in enumerate(줄) if x.startswith("winget install"))
    확인 = next(i for i, x in enumerate(줄[설치:], 설치) if x.startswith("if errorlevel"))
    되감기 = next(i for i, x in enumerate(줄[설치:], 설치) if x == "goto recheck")
    assert 확인 < 되감기  # 성공했는지 보고 나서 다음으로 간다
    assert "goto wingetfailed" in 줄[확인]
    assert ":wingetfailed" in 줄
