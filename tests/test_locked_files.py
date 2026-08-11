"""회사 PC에서 파일이 잠깐 잠기는 것.

실측(2026-08): 파이썬이 파일을 여는 순간 PermissionError [Errno 13]. 권한은
멀쩡했고(icacls: Authenticated Users 수정 가능) 잠시 뒤 같은 파일이 그냥
열렸다. 걸리는 파일도 매번 달랐다 - src/download_slips.py, 다음엔 settings.json.
한 번 실패했다고 단계 전체를 죽이면 안 된다.
"""

import pytest

from src import retry


def _잠긴파일(풀리는횟수: int):
    남은 = [풀리는횟수]

    def 열기():
        if 남은[0] > 0:
            남은[0] -= 1
            raise PermissionError(13, "Permission denied")
        return "내용"

    return 열기


def test_잠깐_잠긴_파일은_다시_열어_성공한다():
    assert retry.keep_trying("settings.json", _잠긴파일(2), waits=(0, 0, 0)) == "내용"


def test_계속_잠겨_있으면_멈춘다():
    """못 읽은 것을 읽은 척하면 안 된다."""
    with pytest.raises(PermissionError) as err:
        retry.keep_trying("settings.json", _잠긴파일(99), waits=(0, 0, 0))
    assert "settings.json" in str(err.value)


def test_왜_이러는지_알려준다():
    """사람이 IT에 무엇을 요청해야 하는지까지 적어야 쓸모가 있다."""
    with pytest.raises(PermissionError) as err:
        retry.keep_trying("x", _잠긴파일(99), waits=(0,))
    말 = str(err.value)
    assert "백신" in 말 and "IT에" in 말


def test_다른_오류는_다시_해보지_않는다():
    """없는 파일을 네 번 여는 것은 시간 낭비다."""
    부른횟수 = []

    def 없는파일():
        부른횟수.append(1)
        raise FileNotFoundError("없다")

    with pytest.raises(FileNotFoundError):
        retry.keep_trying("x", 없는파일, waits=(0, 0, 0))
    assert len(부른횟수) == 1


def test_설정_저장도_같은_규칙을_쓴다(tmp_path, monkeypatch):
    from src import settings

    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    monkeypatch.setattr(retry, "WAITS", (0, 0, 0))

    진짜쓰기 = type(path).write_text
    남은 = [2]

    def 잠긴쓰기(self, *a, **k):
        if 남은[0] > 0:
            남은[0] -= 1
            raise PermissionError(13, "Permission denied")
        return 진짜쓰기(self, *a, **k)

    monkeypatch.setattr(type(path), "write_text", 잠긴쓰기)
    settings.save({"attendee_default": "kyungsik.oh"})
    monkeypatch.setattr(type(path), "write_text", 진짜쓰기)
    assert "kyungsik.oh" in path.read_text(encoding="utf-8")


def test_경로는_프로그램_폴더_기준이다():
    """exe로 묶어 남이 실행하면 현재 폴더가 어디일지 알 수 없다.

    DuoNX 같은 것은 실행 파일만 등록할 수 있고 시작 위치를 못 준다. 그러면
    settings.json 이 엉뚱한 곳에 생기고, 다음 실행 때 설정이 사라진다.
    """
    from pathlib import Path

    from src import paths, settings

    저장소 = Path(__file__).resolve().parent.parent
    assert paths.base() == 저장소
    assert settings.SETTINGS_PATH == 저장소 / "settings.json"
    assert paths.folder("downloads") == 저장소 / "downloads"


def test_직접_고른_폴더는_그대로_쓴다():
    """'찾아보기'로 고른 값은 절대경로다. 기준을 붙이면 망가진다."""
    from pathlib import Path

    from src import paths

    골랐다 = Path("/tmp/전표") if Path("/").exists() else Path("C:/전표")
    assert paths.folder(골랐다) == 골랐다


def test_exe로_묶이면_exe_옆을_본다(monkeypatch):
    """PyInstaller의 _MEIPASS(임시 풀림 폴더)를 쓰면 끝날 때 같이 지워진다."""
    import sys
    from pathlib import Path

    from src import paths

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/앱/Auto-Concur.exe", raising=False)
    assert paths.base() == Path("/opt/앱")


def test_exe_진입점이_인수를_요구하지_않는다():
    """실행 파일만 등록할 수 있는 환경이 있다. `-m src.gui` 를 못 준다."""
    from pathlib import Path

    text = Path("launcher.py").read_text(encoding="utf-8")
    assert "from src.gui import main" in text
    assert "argv" not in text and "argparse" not in text


def test_exe에_단계_모듈이_같이_묶인다():
    """창은 버튼을 누르는 순간 importlib으로 단계를 불러온다.

    PyInstaller는 소스를 읽어서 무엇을 묶을지 정하므로 실행할 때 만들어지는
    이름은 보지 못한다. 그래서 src.gui 하나만 묶였고 첫 버튼이
    "No module named 'src.download_slips'" 로 죽었다.
    """
    import re
    from pathlib import Path

    저장소 = Path(__file__).resolve().parent.parent
    부르는것 = set(re.findall(r'_module\("([a-z_]+)"\)',
                             (저장소 / "src" / "gui.py").read_text(encoding="utf-8")))
    assert 부르는것, "창이 단계를 어떻게 부르는지 못 찾았다"
    for name in 부르는것:
        assert (저장소 / "src" / f"{name}.py").exists(), name

    빌드 = (저장소 / "build_exe.bat").read_text(encoding="ascii")
    assert "--collect-submodules src" in 빌드
    assert "--collect-all playwright" in 빌드


def test_잠긴_파일_이름은_예외에서_가져온다():
    """exe 안에서는 우리가 부른 이름과 실제로 못 연 파일이 다르다.

    모듈 하나에 딸린 DLL 수십 개가 같이 열린다. 부른 이름만 찍었더니
    'src/download_slips.py 가 잠겼다'고 나왔는데 exe 안에는 그런 파일이
    있지도 않았다.
    """
    import pytest as _pytest

    def 딜엘엘이잠김():
        raise PermissionError(13, "Permission denied", r"C:\Temp\_MEI123\pypdf.pyd")

    with _pytest.raises(PermissionError) as err:
        retry.keep_trying("src/download_slips.py", 딜엘엘이잠김, waits=(0,))
    assert "pypdf.pyd" in str(err.value)


def test_이름이_없으면_부른_이름을_쓴다():
    import pytest as _pytest

    with _pytest.raises(PermissionError) as err:
        retry.keep_trying("settings.json", _잠긴파일(99), waits=(0,))
    assert "settings.json" in str(err.value)


def test_나눠줄_때는_파일_하나로_묶는다():
    """동료에게 주는 것은 파일 하나여야 한다. 폴더째 주면 안에서 exe를 찾아야 한다.

    검사가 깐깐한 PC에서는 --onedir 이 낫다(매 실행마다 %TEMP%에 푸는 것을
    검사가 붙잡는다). 그건 그 PC에서 따로 묶는다 - 기본은 나눠주기다.
    """
    from pathlib import Path

    빌드 = (Path(__file__).resolve().parent.parent / "build_exe.bat").read_text(
        encoding="ascii"
    )
    인수 = [x.strip().rstrip("^").strip() for x in 빌드.splitlines()
          if not x.strip().startswith("rem")]
    assert "--onefile" in 인수 and "--onedir" not in 인수
    assert "--noupx" in 인수  # UPX로 줄이면 백신이 더 자주 잡는다


def test_빌드는_이전_결과를_먼저_지운다():
    """--onefile 이 남긴 파일 dist\\Auto-Concur.exe 와 --onedir 이 만들 폴더
    dist\\Auto-Concur 는 이름이 같다. 한쪽 위에 다른 쪽을 만들 수 없다.
    --clean 은 PyInstaller 자기 캐시만 지우고 dist 는 두고 간다.
    """
    from pathlib import Path

    빌드 = (Path(__file__).resolve().parent.parent / "build_exe.bat").read_text(
        encoding="ascii"
    )
    명령 = [x.strip() for x in 빌드.splitlines() if not x.strip().startswith("rem")]
    지우기 = next(i for i, x in enumerate(명령) if "rmdir /s /q dist" in x)
    묶기 = next(i for i, x in enumerate(명령) if x.endswith("-m PyInstaller ^"))
    assert 지우기 < 묶기  # 빌드하기 전에 지워야 한다


def test_빌드_도구를_최신으로_올린다():
    """PyInstaller는 자기가 묶는 파이썬을 알아야 한다.

    실측: 파이썬 3.14.5를 낡은 PyInstaller로 묶었더니 exe가
    'Failed to import encodings module' 로 시작조차 못 했다.
    """
    from pathlib import Path

    빌드 = (Path(__file__).resolve().parent.parent / "build_exe.bat").read_text(
        encoding="ascii"
    )
    assert "pip install --upgrade pyinstaller" in 빌드
    assert "-m PyInstaller --version" in 빌드  # 안 될 때 제일 먼저 볼 것


def test_껍데기_exe는_venv를_인수와_함께_부른다():
    """PyInstaller 없이 실행 파일을 만드는 길.

    실행 파일만 등록할 수 있는 환경에서 필요한 것은 '인수를 대신 넣어주는
    것' 하나다. 파이썬을 통째로 묶을 이유가 없었고, 묶었더니 인터프리터가
    시작조차 못 했다('No module named encodings').
    """
    from pathlib import Path

    저장소 = Path(__file__).resolve().parent.parent
    소스 = (저장소 / "launcher.cs").read_text(encoding="utf-8")
    assert '"venv", "Scripts", "python.exe"' in 소스
    assert '"-m src.gui"' in 소스
    # 시작 위치를 못 박아야 settings.json 이 엉뚱한 데 생기지 않는다
    assert "WorkingDirectory = here" in 소스

    빌드 = (저장소 / "make_exe.bat").read_text(encoding="ascii")
    assert "csc.exe" in 빌드  # Windows에 원래 있는 컴파일러
    assert "pip install" not in 빌드  # 막힌 PC에서도 돌아야 한다


def test_얼마나_기다렸는지_알려준다():
    """'다시 시도합니다'만 네 줄 찍히고 끝나면 얼마나 버틴 건지 알 수 없다."""
    import pytest as _pytest

    with _pytest.raises(PermissionError) as err:
        retry.keep_trying("x.py", _잠긴파일(99), waits=(0.01, 0.01))
    assert "3번" in str(err.value)


def test_15초쯤_버틴다():
    """5초로는 모자란 파일이 있었다. 검사가 처음 보는 파일은 오래 붙잡는다."""
    assert 12 <= sum(retry.WAITS) <= 20


def test_미리_읽어두는_단계가_실제_단계와_같다():
    """미리 읽는 목록이 버튼이 부르는 것과 어긋나면 미리 읽는 의미가 없다."""
    import re
    from pathlib import Path

    저장소 = Path(__file__).resolve().parent.parent
    소스 = (저장소 / "src" / "gui.py").read_text(encoding="utf-8")

    부르는것 = set(re.findall(r'_module\("([a-z_]+)"', 소스))
    미리 = set(re.findall(r'"([a-z_]+)",', 소스.split("STEP_MODULES = (")[1].split(")")[0]))
    미리 |= set(re.findall(r'"([a-z_]+)"', 소스.split("STEP_MODULES = (")[1].split(")")[0]))
    assert 부르는것 <= 미리, f"미리 안 읽는 단계: {부르는것 - 미리}"


def test_미리_읽기는_조용히_실패한다():
    """사람이 시키지 않은 일이 안 된다고 떠들면 진짜 메시지가 묻힌다."""
    import io
    import contextlib

    import pytest as _pytest

    말한것 = io.StringIO()
    with contextlib.redirect_stdout(말한것):
        with _pytest.raises(PermissionError):
            retry.keep_trying("x.py", _잠긴파일(99), waits=(0, 0), quiet=True)
    assert 말한것.getvalue() == ""

    말한것 = io.StringIO()
    with contextlib.redirect_stdout(말한것):
        with _pytest.raises(PermissionError):
            retry.keep_trying("x.py", _잠긴파일(99), waits=(0, 0))
    assert "다시 시도합니다" in 말한것.getvalue()


def test_전표_폴더_기본값은_프로그램_폴더다():
    """exe를 받은 사람은 그 폴더에 넣고 쓴다. 전표도 거기 있는 것이 자연스럽다."""
    from src import paths, settings

    assert settings.DEFAULTS["downloads_dir"] == ""
    assert paths.folder(settings.DEFAULTS["downloads_dir"]) == paths.base()


def test_창은_고른_폴더에서_시작한다():
    """없는 경로를 주면 파일 창이 엉뚱한 데서 열린다."""
    from pathlib import Path

    # tkinter 없는 곳에서도 돌아야 해서 소스를 읽는다
    소스 = (Path(__file__).resolve().parent.parent / "src" / "gui.py").read_text(
        encoding="utf-8"
    )
    고르기 = 소스.split("def pick_folder")[1].split("def ")[0]
    assert "paths.base()" in 고르기
    assert "is_dir()" in 고르기  # 적힌 폴더가 없으면 프로그램 폴더로 떨어진다
    assert "initialdir=str(here)" in 고르기


def test_지금_도는_코드가_언제_것인지_보여준다():
    """'pull 했는데 왜 그대로냐', '빌드를 다시 해야 하냐' 를 눈으로 가린다."""
    import re
    from pathlib import Path

    from src import paths

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", paths.stamp())

    소스 = (Path(__file__).resolve().parent.parent / "src" / "gui.py").read_text(
        encoding="utf-8"
    )
    assert "paths.stamp()" in 소스  # 창을 열면 바로 보여야 한다
