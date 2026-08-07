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
