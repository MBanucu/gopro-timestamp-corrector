import tkinter as tk
from tkinter import ttk

from gui.calibration_panel import CalibrationPanel


class StepCalibration(ttk.Frame):
    def __init__(self, parent, *, get_dir_fn=None, log_fn=None,
                 set_status_fn=None, delta_changed_cb=None,
                 on_skip_to_run=None, **kw):
        super().__init__(parent, **kw)
        self._log = log_fn or (lambda m: None)
        self._set_status = set_status_fn or (lambda m: None)

        ttk.Label(self, text='2. Calibration',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        self.cal_panel = CalibrationPanel(
            self,
            get_dir_fn=get_dir_fn,
            log_fn=self._log,
            set_status_fn=self._set_status,
            delta_changed_cb=delta_changed_cb,
        )
        self.cal_panel.pack(fill=tk.X)

        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, pady=(12, 0))
        self._next_btn = ttk.Button(nav, text='Review Files \u2192',
                                    command=lambda: self._on_next(), width=16)
        self._next_btn.pack(side=tk.RIGHT, padx=(4, 0))
        self._skip_btn = ttk.Button(nav, text='Skip to Run \u2192',
                                    command=lambda: self._on_skip(), width=16)
        self._skip_btn.pack(side=tk.RIGHT)

    _on_next = lambda self: None
    _on_skip = lambda self: None

    def set_on_next(self, cb):
        self._on_next = cb

    def set_on_skip(self, cb):
        self._on_skip = cb

    def auto_calibrate(self):
        """Trigger auto-calibration from GPS data."""
        self.cal_panel.auto_calibrate()

    @property
    def manual_delta(self):
        return self.cal_panel.manual_delta
