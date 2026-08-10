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
