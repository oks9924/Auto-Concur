"""단위 테스트가 실제 탐색기 창을 열지 않게 한다."""
import pytest
from src import console


@pytest.fixture(autouse=True)
def no_folder_windows(monkeypatch):
    monkeypatch.setattr(console, 'open_folder', lambda path: None)


def _dispose_tk(root):
    """Cancel callbacks through the widget which owns their Tcl command.

    root.after_cancel(child_job) deletes the command without updating the
    child's _tclCommands, so child.destroy then fails and leaves modal grabs.
    This helper changes test cleanup only; assertions and Tk grabs stay real.
    """
    if not root.winfo_exists():
        return

    def walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from walk(child)

    owners = {command: widget for widget in walk(root)
              for command in (widget._tclCommands or [])}
    for job in root.tk.call('after', 'info'):
        script = root.tk.call('after', 'info', job)[0]
        parts = root.tk.splitlist(script)
        owner = owners.get(parts[0] if parts else '')
        if owner is not None:
            owner.after_cancel(job)
        else:
            # Tcl-owned callback: cancel timer without deleting a Python command.
            root.tk.call('after', 'cancel', job)
    grabbed = root.tk.call('grab', 'current', root._w)
    if grabbed:
        root.tk.call('grab', 'release', grabbed)
    root.destroy()


@pytest.fixture
def tk_cleanup():
    """Register standalone App roots so failures cannot leak them to later tests."""
    import gc
    roots = []
    gc.collect()
    yield lambda root: roots.append(root)
    try:
        for root in reversed(roots):
            _dispose_tk(root)
    finally:
        roots.clear()
        gc.collect()


@pytest.fixture
def tk_window(tk_cleanup):
    import tkinter as tk
    window = tk.Tk()
    tk_cleanup(window)
    return window
