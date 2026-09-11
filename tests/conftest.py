"""단위 테스트가 실제 탐색기 창을 열지 않게 한다."""
import pytest
from src import console


@pytest.fixture(autouse=True)
def no_folder_windows(monkeypatch):
    monkeypatch.setattr(console, 'open_folder', lambda path: None)
