"""Keep tksheet's native Tk text editor but make it look like the cell it edits.

Windows Korean IME composes into a real Tk Text widget.  Styling that widget
flat and with the worksheet font avoids the conspicuous default white editor
box while preserving tksheet's undo/paste/edit event semantics.
"""
import tkinter as tk

TABLE_BG = '#ffffff'
TABLE_FG = '#17212e'
SELECT_BG = '#dcecff'
SELECT_FG = '#17212e'


def style_text_editor(sheet, font):
    try:
        editor = sheet.get_text_editor_widget()
    except (AttributeError, tk.TclError):
        return False
    if editor is None:
        return False
    try:
        editor.configure(
            font=font,
            background=TABLE_BG,
            foreground=TABLE_FG,
            insertbackground=TABLE_FG,
            selectbackground=SELECT_BG,
            selectforeground=SELECT_FG,
            relief='flat',
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
        )
        return True
    except tk.TclError:
        return False


def schedule_style(owner, sheet, font):
    """The begin-edit callback fires before tksheet has created its Text widget."""
    def apply():
        if owner.winfo_exists():
            style_text_editor(sheet, font)
    for delay in (0, 10, 40):
        owner.after(delay, apply)
