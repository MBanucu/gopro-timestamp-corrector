import tkinter as tk
from tkinter import ttk

from gui.file_table import FileSetTable


class StepReview(ttk.Frame):
    def __init__(self, parent, *, manual_delta_changed_cb=None, **kw):
        super().__init__(parent, **kw)

        ttk.Label(self, text='3. Review Files',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        table_frame = ttk.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True)

        self.file_table = FileSetTable(
            table_frame,
            manual_delta_changed_cb=manual_delta_changed_cb,
        )
        self.file_table.pack(fill=tk.BOTH, expand=True)

        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, pady=(8, 0))
        self._back_btn = ttk.Button(nav, text='\u2190 Back',
                                    command=lambda: self._on_back(), width=10)
        self._back_btn.pack(side=tk.LEFT)
        self._next_btn = ttk.Button(nav, text='Proceed to Run \u2192',
                                    command=lambda: self._on_next(), width=18)
        self._next_btn.pack(side=tk.RIGHT)

    _on_back = lambda self: None
    _on_next = lambda self: None

    def set_on_back(self, cb):
        self._on_back = cb

    def set_on_next(self, cb):
        self._on_next = cb

    def load_analysis(self, analysis):
        self.file_table.load_analysis(analysis)

    @property
    def manual_delta(self):
        return self.file_table.manual_delta

    @manual_delta.setter
    def manual_delta(self, delta):
        self.file_table.manual_delta = delta

    def get_write_jobs(self):
        return self.file_table.get_write_jobs()

    def get_decisions(self):
        return self.file_table.get_decisions()

    def clear(self):
        self.file_table.clear()
