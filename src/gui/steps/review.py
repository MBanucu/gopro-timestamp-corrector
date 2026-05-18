import tkinter as tk
from tkinter import ttk

from gui.file_table import FileSetTable
from gui.calibration_panel import CalibrationPanel


class StepReview(ttk.Frame):
    def __init__(self, parent, *, get_dir_fn=None, log_fn=None,
                 set_status_fn=None, delta_changed_cb=None, **kw):
        super().__init__(parent, **kw)

        ttk.Label(self, text='2. Review & Calibration',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        top = ttk.Frame(self)
        top.pack(fill=tk.X, pady=(0, 6))

        self.cal_panel = CalibrationPanel(
            top,
            get_dir_fn=get_dir_fn,
            log_fn=log_fn or (lambda m: None),
            set_status_fn=set_status_fn or (lambda m: None),
            delta_changed_cb=delta_changed_cb,
        )
        self.cal_panel.pack(fill=tk.X)

        sep = ttk.Separator(self, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=(0, 6))

        table_frame = ttk.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True)

        self.file_table = FileSetTable(table_frame)
        self.file_table.pack(fill=tk.BOTH, expand=True)

        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, pady=(8, 0))
        self._back_btn = ttk.Button(nav, text='\u2190 Back',
                                    command=lambda: self._on_back(), width=10)
        self._back_btn.pack(side=tk.LEFT)
        self._next_btn = ttk.Button(nav, text='Proceed to Plan \u2192',
                                    command=lambda: self._on_next(), width=18)
        self._next_btn.pack(side=tk.RIGHT)

    _on_back = lambda self: None
    _on_next = lambda self: None

    def set_on_back(self, cb):
        self._on_back = cb

    def set_on_next(self, cb):
        self._on_next = cb

    @property
    def plan(self):
        return self.file_table.plan

    def load_analysis(self, analysis):
        self.file_table.load_analysis(analysis)

    def auto_calibrate(self):
        self.cal_panel.auto_calibrate()

    @property
    def manual_delta(self):
        return self.file_table.manual_delta

    @manual_delta.setter
    def manual_delta(self, delta):
        self.file_table.manual_delta = delta

    def get_write_jobs(self):
        return self.file_table.get_write_jobs()

    def clear(self):
        self.file_table.clear()
