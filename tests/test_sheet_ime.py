import tkinter as tk

from src.sheet_ime import style_text_editor


class FakeSheet:
    def __init__(self, widget):
        self.widget = widget
    def get_text_editor_widget(self):
        return self.widget


def test_ime_editor_is_flat_and_uses_korean_font(tk_window):
    editor = tk.Text(tk_window, relief='sunken', borderwidth=2, highlightthickness=2)
    sheet = FakeSheet(editor)
    assert style_text_editor(sheet, ('맑은 고딕', 11, 'normal'))
    assert editor.cget('relief') == 'flat'
    assert int(editor.cget('borderwidth')) == 0
    assert int(editor.cget('highlightthickness')) == 0
    assert editor.cget('background') == '#ffffff'
    assert editor.cget('foreground') == '#17212e'
    assert '맑은 고딕' in str(editor.cget('font'))


def test_missing_editor_is_safe():
    assert style_text_editor(FakeSheet(None), ('맑은 고딕', 11, 'normal')) is False
