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


class 가짜페이지:
    def __init__(self, url=""):
        self.url = url
        self.closed = False
        self.front = False

    def close(self):
        self.closed = True

    def bring_to_front(self):
        self.front = True


class 가짜컨텍스트:
    """pages가 처음엔 비었다가 잠시 뒤 채워지는 브라우저를 흉내낸다."""

    def __init__(self, 늦게=None, 처음부터=None):
        self._늦게 = 늦게 or []
        self.pages = list(처음부터 or [])
        self.만든것 = []
        self._남은지연 = 2 if 늦게 else 0

    def __getattribute__(self, name):
        if name == "pages":
            지연 = object.__getattribute__(self, "_남은지연")
            if 지연:
                object.__setattr__(self, "_남은지연", 지연 - 1)
                return []
            return object.__getattribute__(self, "pages")
        return object.__getattribute__(self, name)

    def new_page(self):
        page = 가짜페이지()
        self.만든것.append(page)
        object.__getattribute__(self, "pages").append(page)
        return page


def test_창이_늦게_떠도_새로_만들지_않는다(monkeypatch):
    """실측: 빈 화면이 떴다가, 닫으니까 그제서야 현대카드가 떴다.

    브라우저가 시작 페이지를 그리기 전에 물어서 창이 없는 줄 알고 하나 더
    만들었고, 그 빈 창이 우리 창을 덮었다.
    """
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    이미있던 = 가짜페이지("about:blank")
    ctx = 가짜컨텍스트(늦게=True, 처음부터=[이미있던])

    page = browser.first_page(ctx)
    assert page is 이미있던
    assert ctx.만든것 == []  # 기다렸으면 만들 이유가 없다
    assert page.front  # 뒤에 가려지지 않게 앞으로 올린다


def test_남는_빈_창은_닫는다(monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    쓸것, 빈것 = 가짜페이지("about:blank"), 가짜페이지("about:blank")
    ctx = 가짜컨텍스트(처음부터=[쓸것, 빈것])

    assert browser.first_page(ctx) is 쓸것
    assert 빈것.closed


def test_내용이_있는_창은_두고_간다(monkeypatch):
    """사람이 보던 탭일 수 있다. 우리가 닫을 것이 아니다."""
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    쓸것, 남길것 = 가짜페이지("about:blank"), 가짜페이지("https://example.com")
    ctx = 가짜컨텍스트(처음부터=[쓸것, 남길것])

    browser.first_page(ctx)
    assert not 남길것.closed


def test_끝내_없으면_하나_만든다(monkeypatch):
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)
    ctx = 가짜컨텍스트()
    page = browser.first_page(ctx, wait_ms=200)
    assert page in ctx.만든것


class 늦게여는컨텍스트(가짜컨텍스트):
    """이동한 뒤에 브라우저가 자기 시작 탭을 여는 경우."""

    def __init__(self, 쓸것):
        super().__init__(처음부터=[쓸것])
        self.늦게연것 = None

    def 늦게열기(self):
        self.늦게연것 = 가짜페이지("about:blank")
        object.__getattribute__(self, "pages").append(self.늦게연것)


def test_이동한_뒤에_뜨는_빈_창도_치운다(monkeypatch):
    """실측: 빈 창을 닫아야 진짜 창이 뜬다. 창을 고른 뒤에 열려서 덮은 것이다."""
    쓸것 = 가짜페이지("about:blank")
    ctx = 늦게여는컨텍스트(쓸것)

    def goto(url, wait_until=None):
        쓸것.url = url
        ctx.늦게열기()  # 이동하는 사이에 시작 탭이 뜬다

    쓸것.goto = goto
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)

    page = browser.open_first(ctx, "https://example.com")
    assert page is 쓸것
    assert ctx.늦게연것.closed
    assert 쓸것.front


def test_내용이_생긴_창은_뒤늦게도_닫지_않는다(monkeypatch):
    """전표 인쇄 팝업은 빈 창으로 열렸다가 내용이 찬다. 그건 우리가 쓸 창이다."""
    쓸것 = 가짜페이지("about:blank")
    ctx = 늦게여는컨텍스트(쓸것)

    def goto(url, wait_until=None):
        쓸것.url = url
        ctx.늦게열기()
        ctx.늦게연것.url = "https://popup.example.com/print"

    쓸것.goto = goto
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)

    browser.open_first(ctx, "https://example.com")
    assert not ctx.늦게연것.closed


def test_한_번만_치우지_않는다(monkeypatch):
    """시작 탭이 이동보다 한참 늦게 뜨는 경우가 있다. 잠시 지켜봐야 잡힌다."""
    쓸것 = 가짜페이지("about:blank")
    ctx = 늦게여는컨텍스트(쓸것)
    쓸것.goto = lambda url, wait_until=None: setattr(쓸것, "url", url)

    잔여 = [3]

    def 늦게(초):
        잔여[0] -= 1
        if 잔여[0] == 0:  # 몇 번째 확인에서야 뜬다
            ctx.늦게열기()

    monkeypatch.setattr(browser.time, "sleep", 늦게)
    browser.open_first(ctx, "https://example.com")
    assert ctx.늦게연것.closed


def test_남은_창을_로그에_적는다(monkeypatch, capsys):
    """빈 창이 남는다면 우리가 못 보는 창이라는 뜻이다. 그 판단에 필요한 근거다."""
    쓸것 = 가짜페이지("about:blank")
    ctx = 가짜컨텍스트(처음부터=[쓸것, 가짜페이지("https://other.example")])
    쓸것.goto = lambda url, wait_until=None: setattr(쓸것, "url", url)
    monkeypatch.setattr(browser.time, "sleep", lambda s: None)

    browser.open_first(ctx, "https://example.com")
    말 = capsys.readouterr().out
    assert "브라우저 창" in 말 and "https://other.example" in 말


class 가짜플레이라이트:
    """Chromium이 아직 없는 PC. 받고 나면 열린다."""

    def __init__(self, 받으면열림=True):
        self.있음 = False
        self.받으면열림 = 받으면열림
        self.시도 = []

    @property
    def chromium(self):
        return self

    def launch_persistent_context(self, **kw):
        channel = kw.get("channel")
        self.시도.append(channel)
        if channel is None and not self.있음:
            raise RuntimeError(
                "BrowserType.launchPersistentContext: Executable doesn't exist at "
                r"C:\Users\x\AppData\Local\ms-playwright\chromium-1234\chrome.exe"
            )
        if channel is not None:
            raise RuntimeError("BrowserType.launch: Executable doesn't exist")
        return "열림"


def test_Chromium이_없으면_받아서_연다(monkeypatch):
    """exe에는 브라우저가 안 들어간다. 처음 한 번 받으면 그 뒤로는 그냥 열린다."""
    pw = 가짜플레이라이트()

    def 받기():
        pw.있음 = True
        return None

    monkeypatch.setattr(browser, "install_chromium", 받기)
    assert browser.launch(pw, "/tmp/p") == "열림"
    assert pw.시도 == [None, None]  # 받기 전 한 번, 받고 나서 한 번


def test_한_번만_받아본다(monkeypatch):
    """못 받는 PC에서 채널마다 다시 받으려 들면 몇 분씩 붙잡힌다."""
    pw = 가짜플레이라이트()
    부른횟수 = []

    monkeypatch.setattr(browser, "install_chromium",
                        lambda: 부른횟수.append(1) or "회사망이 막았습니다")
    try:
        browser.launch(pw, "/tmp/p")
    except RuntimeError as exc:
        assert "회사망이 막았습니다" in str(exc)
    assert len(부른횟수) == 1


def test_이미_있으면_받지_않는다(monkeypatch):
    """setup.bat 을 거친 PC는 이 길로 오지 않는다."""
    pw = 가짜플레이라이트()
    pw.있음 = True
    monkeypatch.setattr(browser, "install_chromium",
                        lambda: (_ for _ in ()).throw(AssertionError("받으면 안 된다")))
    assert browser.launch(pw, "/tmp/p") == "열림"
