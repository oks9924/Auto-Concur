"""Local scroll container; no application-wide mouse-wheel bindings."""
import tkinter as tk
from tkinter import ttk


class ScrollArea(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1); self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, background='#f5f7fb')
        self.canvas.grid(row=0, column=0, sticky='nsew')
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.canvas.configure(yscrollcommand=bar.set)
        self.body = ttk.Frame(self.canvas, padding=10)
        self.item = self.canvas.create_window(0, 0, window=self.body, anchor='nw')
        self.body.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(self.item, width=event.width))
        self.canvas.bind('<MouseWheel>', self.wheel)
        self.canvas.bind('<Button-4>', lambda event: self.canvas.yview_scroll(-3, 'units'))
        self.canvas.bind('<Button-5>', lambda event: self.canvas.yview_scroll(3, 'units'))

    def wheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1), 'units')
        return 'break'

    def enable_children(self):
        def visit(widget):
            # Leave text editing and combobox wheel behavior intact.
            if not isinstance(widget, (tk.Text, ttk.Combobox, ttk.Entry)):
                widget.bind('<MouseWheel>', self.wheel, add='+')
            widget.bind('<FocusIn>', lambda event, w=widget: self.reveal(w), add='+')
            for child in widget.winfo_children():
                visit(child)
        visit(self.body)

    def reveal(self, widget):
        if not widget.winfo_exists():
            return
        y = widget.winfo_rooty() - self.body.winfo_rooty()
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        total = max(1, self.body.winfo_height())
        if y < top:
            self.canvas.yview_moveto(max(0, y - 8) / total)
        elif y + widget.winfo_height() > top + height:
            self.canvas.yview_moveto(max(0, y + widget.winfo_height() - height + 8) / total)
