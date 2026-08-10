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
