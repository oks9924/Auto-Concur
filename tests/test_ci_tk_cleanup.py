import tkinter as tk
from conftest import _dispose_tk


def test_cleanup_cancels_child_callbacks_with_their_owner(tk_window):
    child = tk.Toplevel(tk_window)
    calls = []
    child.after(60000, lambda: calls.append('child'))
    tk_window.after(60000, lambda: calls.append('root'))
    child.grab_set()
    _dispose_tk(tk_window)
    assert child._tclCommands is None and tk_window._tclCommands is None
    assert calls == []


def test_fixture_releases_modal_between_tests(tk_window):
    child = tk.Toplevel(tk_window)
    child.grab_set()
    assert tk_window.grab_current() is child
    # Deliberately let fixture clean up rather than hiding leaks by manual close.


def test_next_root_has_no_prior_modal(tk_window):
    assert tk_window.grab_current() is None
