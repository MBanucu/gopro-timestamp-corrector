import tkinter as tk
from tkinter import ttk

from options import CAL_TIME_LABEL


class TimeSelector(ttk.Frame):
    def __init__(self, parent, *, label='Time:', show_seconds=True,
                 show_ms=True, label_width=8, **kw):
        super().__init__(parent, **kw)

        self._show_seconds = show_seconds
        self._show_ms = show_ms and show_seconds

        ttk.Label(self, text=label, width=label_width).pack(side=tk.LEFT)

        self.hour_var = tk.StringVar(value='00')
        ttk.Spinbox(self, textvariable=self.hour_var, from_=0, to=23,
                    width=2, format='%02.0f').pack(side=tk.LEFT)
        self._sep1 = ttk.Label(self, text=':')
        self._sep1.pack(side=tk.LEFT)

        self.min_var = tk.StringVar(value='00')
        ttk.Spinbox(self, textvariable=self.min_var, from_=0, to=59,
                    width=2, format='%02.0f').pack(side=tk.LEFT)

        if show_seconds:
            self._sep2 = ttk.Label(self, text=':')
            self._sep2.pack(side=tk.LEFT)
            self.sec_var = tk.StringVar(value='00')
            ttk.Spinbox(self, textvariable=self.sec_var, from_=0, to=59,
                        width=2, format='%02.0f').pack(side=tk.LEFT)

            if self._show_ms:
                self._sep3 = ttk.Label(self, text='.')
                self._sep3.pack(side=tk.LEFT)
                self.ms_var = tk.StringVar(value='000')
                ttk.Spinbox(self, textvariable=self.ms_var, from_=0, to=999,
                            width=3, format='%03.0f').pack(side=tk.LEFT)

        ttk.Label(self, text=CAL_TIME_LABEL, foreground='gray').pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

    def set_time(self, hour=0, minute=0, second=0, ms=0):
        self.hour_var.set(f'{int(hour):02d}')
        self.min_var.set(f'{int(minute):02d}')
        if hasattr(self, 'sec_var'):
            self.sec_var.set(f'{int(second):02d}')
        if hasattr(self, 'ms_var'):
            self.ms_var.set(f'{int(ms):03d}')

    def get_time(self):
        h = int(self.hour_var.get() or '0')
        m = int(self.min_var.get() or '0')
        s = int(getattr(self, 'sec_var', None) and (self.sec_var.get() or '0') or '0')
        ms = int(getattr(self, 'ms_var', None) and (self.ms_var.get() or '0') or '0')
        return h, m, s, ms
