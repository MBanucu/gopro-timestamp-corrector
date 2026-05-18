import tkinter as tk
from tkinter import ttk

from options import BTIME_GUI_CHOICES


class StepPlan(ttk.Frame):
    def __init__(self, parent, *, on_back=None, on_next=None, **kw):
        super().__init__(parent, **kw)
        self._on_back = on_back or (lambda: None)
        self._on_next = on_next or (lambda: None)

        ttk.Label(self, text='3. Plan',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        back_row = ttk.Frame(self)
        back_row.pack(fill=tk.X, pady=(0, 6))
        back_link = ttk.Label(back_row, text='\u2190 Back to Review',
                              foreground='#07c', cursor='hand2', font=('', 9))
        back_link.pack(side=tk.LEFT)
        back_link.bind('<Button-1>', lambda e: self._on_back())

        opt = ttk.LabelFrame(self, text='Corrections', padding=8)
        opt.pack(fill=tk.X, pady=(0, 6))

        self.fix_embedded_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text='EXIF / QuickTime metadata',
                        variable=self.fix_embedded_var).pack(anchor=tk.W,
                                                             pady=1)

        self.fix_mtime_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text='Filesystem modification time (mtime)',
                        variable=self.fix_mtime_var).pack(anchor=tk.W, pady=1)

        btime_row = ttk.Frame(opt)
        btime_row.pack(fill=tk.X, pady=1)
        self.fix_btime_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(btime_row, text='Filesystem birth time (btime)',
                        variable=self.fix_btime_var,
                        command=self._toggle_btime).pack(side=tk.LEFT)
        self.btime_method_var = tk.StringVar(value='auto')
        self.btime_combo = ttk.Combobox(btime_row,
                                        textvariable=self.btime_method_var,
                                        state='readonly', width=14)
        self.btime_combo['values'] = BTIME_GUI_CHOICES
        self.btime_combo.pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(btime_row,
                  text='ext4\u2192debugfs  exFAT\u2192exfat_raw',
                  foreground='gray').pack(side=tk.LEFT)

        self._toggle_btime()

        sep = ttk.Separator(opt, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=6)

        flags = ttk.Frame(opt)
        flags.pack(fill=tk.X, pady=2)
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(flags, text='Dry run (preview, no writes)',
                        variable=self.dry_run_var).pack(side=tk.LEFT,
                                                        padx=(0, 16))
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags, text='Force (ignore manifest)',
                        variable=self.force_var).pack(side=tk.LEFT)

        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(nav, text='Proceed to Run \u2192',
                   command=self._on_next).pack(side=tk.RIGHT)

    def _toggle_btime(self):
        state = tk.NORMAL if self.fix_btime_var.get() else tk.DISABLED
        self.btime_combo.config(state=state)

    def get_options(self) -> dict:
        return {
            'fix_embedded': self.fix_embedded_var.get(),
            'fix_mtime': self.fix_mtime_var.get(),
            'fix_btime': self.btime_method_var.get()
                         if self.fix_btime_var.get()
                         else 'off',
            'dry_run': self.dry_run_var.get(),
            'force': self.force_var.get(),
        }
