import tkinter as tk
from tkinter import ttk


class StepRun(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)

        ttk.Label(self, text='4. Run',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        back_row = ttk.Frame(self)
        back_row.pack(fill=tk.X, pady=(0, 6))
        self._back_link = ttk.Label(back_row, text='\u2190 Back to Plan',
                                    foreground='#07c', cursor='hand2',
                                    font=('', 9))
        self._back_link.pack(side=tk.LEFT)
        self._back_link.bind('<Button-1>', lambda e: self._on_back())

        summary_frame = ttk.LabelFrame(self, text='Plan summary', padding=8)
        summary_frame.pack(fill=tk.X, pady=(0, 6))
        self._summary_var = tk.StringVar(value='No plan loaded.')
        ttk.Label(summary_frame, textvariable=self._summary_var,
                  wraplength=700).pack(anchor=tk.W)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, pady=4)
        self.apply_btn = ttk.Button(btn_row, text='Apply', width=12)
        self.apply_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btn_row, text='Cancel', width=10,
                                     state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT)

    _on_back = lambda self: None

    def set_on_back(self, cb):
        self._on_back = cb

    def set_commands(self, *, apply=None, cancel=None):
        if apply:
            self.apply_btn.config(command=apply)
        if cancel:
            self.cancel_btn.config(command=cancel)

    def set_summary(self, text: str):
        self._summary_var.set(text)

    def set_buttons_enabled(self, enabled):
        self.apply_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def set_cancel_enabled(self, enabled):
        self.cancel_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)
