"""받은 파일을 무엇으로 볼 것인가.

실측 2026-09-09: 이름은 PDF인데 내용이 ZIP이었다(PK\x03\x04). pypdf 가
'Stream has ended unexpectedly' 로 죽었고, 그 메시지만으로는 무엇을 받았는지
알 수 없었다. 확장자를 믿지 말고 앞부분을 보고 정한다.
"""

import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from src.download_slips import DownloadError, _kind, _split


def 한장짜리(path: Path, pages: int = 1) -> Path:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    with path.open("wb") as f:
        w.write(f)
    return path


def test_무엇을_받았는지_앞부분으로_안다(tmp_path):
    assert _kind(한장짜리(tmp_path / "a.pdf")) == "pdf"

    zip_path = tmp_path / "b.pdf"  # 이름은 pdf인데 내용은 zip이었다
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("x.pdf", b"%PDF-1.4\n")
    assert _kind(zip_path) == "zip"

    (tmp_path / "c.pdf").write_bytes(b"<!DOCTYPE html>")
    assert _kind(tmp_path / "c.pdf") not in ("pdf", "zip")


def test_압축으로_와도_전표를_꺼낸다(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    bundle = tmp_path / "매출전표_20260909.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for i in range(3):
            zf.writestr(f"slip{i}.pdf", 한장짜리(tmp_path / f"s{i}.pdf").read_bytes())

    assert _split(bundle, out, expected=3) == 3
    assert sorted(p.name for p in out.glob("*.pdf")) == [
        "slip_001.pdf", "slip_002.pdf", "slip_003.pdf"
    ]


def test_압축_안에_합본_한_장이면_쪼갠다(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    bundle = tmp_path / "b.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("all.pdf", 한장짜리(tmp_path / "all.pdf", pages=3).read_bytes())

    assert _split(bundle, out, expected=3) == 3
    assert len(list(out.glob("slip_*.pdf"))) == 3


def test_개수가_다르면_멈춘다(tmp_path):
    """전표와 거래가 1:1이 아니면 이후 매칭을 믿을 수 없다."""
    out = tmp_path / "out"
    out.mkdir()
    bundle = tmp_path / "b.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("a.pdf", 한장짜리(tmp_path / "a.pdf").read_bytes())
        zf.writestr("b.pdf", 한장짜리(tmp_path / "b2.pdf").read_bytes())

    with pytest.raises(DownloadError, match="1:1"):
        _split(bundle, out, expected=5)
    assert not list(out.glob("*.pdf"))  # 하나도 쓰지 않는다


def test_압축_안의_이름을_경로로_쓰지_않는다(tmp_path):
    """'../' 같은 이름이 들어 있으면 엉뚱한 데 쓰게 된다."""
    out = tmp_path / "out"
    out.mkdir()
    bundle = tmp_path / "b.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        for name in ("../달아난다.pdf", "또다른.pdf"):
            zf.writestr(name, 한장짜리(tmp_path / "x.pdf").read_bytes())

    assert _split(bundle, out, expected=2) == 2
    assert not (tmp_path / "달아난다.pdf").exists()
    assert sorted(p.name for p in out.glob("*.pdf")) == ["slip_001.pdf", "slip_002.pdf"]


def test_PDF도_압축도_아니면_무엇인지_말한다(tmp_path):
    """로그인이 풀려 오류 페이지를 받는 일이 있다. 그때 pypdf 예외만 뜨면 알 수 없다."""
    out = tmp_path / "out"
    out.mkdir()
    bad = tmp_path / "받은것.pdf"
    bad.write_bytes(b"<!DOCTYPE html><html>session expired")

    with pytest.raises(DownloadError) as err:
        _split(bad, out, expected=1)
    assert "PDF도 압축도 아닙니다" in str(err.value)
    assert "받은것.pdf" in str(err.value)


def test_압축에_PDF가_없으면_들어_있던_것을_보여준다(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    bundle = tmp_path / "b.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.writestr("안내.txt", b"no slips")

    with pytest.raises(DownloadError) as err:
        _split(bundle, out, expected=1)
    assert "안내.txt" in str(err.value)
