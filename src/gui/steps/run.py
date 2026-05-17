import tkinter as tk
from tkinter import ttk, scrolledtext


class StepRun(ttk.Frame):
    def __init__(self, parent, *, log_fn=None, set_status_fn=None, **kw):
        super().__init__(parent, **kw)
        self._log_fn = log_fn or (lambda m: None)
        self._set_status_fn = set_status_fn or (lambda m: None)

        ttk.Label(self, text='4. Run',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        back_row = ttk.Frame(self)
        back_row.pack(fill=tk.X, pady=(0, 6))
        self._back_link = ttk.Label(back_row, text='\u2190 Back to Review',
                                    foreground='#07c', cursor='hand2',
                                    font=('', 9))
        self._back_link.pack(side=tk.LEFT)
        self._back_link.bind('<Button-1>', lambda e: self._on_back())

        opt = ttk.LabelFrame(self, text='Options', padding=8)
        opt.pack(fill=tk.X, pady=(0, 6))

        btime_row = ttk.Frame(opt)
        btime_row.pack(fill=tk.X, pady=2)
        ttk.Label(btime_row, text='Fix btime:', width=14).pack(side=tk.LEFT)
        self.btime_var = tk.StringVar(value='off')
        bm = ttk.Combobox(btime_row, textvariable=self.btime_var,
                          state='readonly', width=14)
        bm['values'] = ('off', 'auto', 'debugfs', 'fuse', 'clock')
        bm.pack(side=tk.LEFT)
        ttk.Label(btime_row,
                  text='  ext4\u2192debugfs  exFAT\u2192fuse  fallback\u2192clock',
                  foreground='gray').pack(side=tk.LEFT)

        flags1 = ttk.Frame(opt)
        flags1.pack(fill=tk.X, pady=2)
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(flags1, text='Dry run',
                        variable=self.dry_run_var).pack(side=tk.LEFT,
                                                       padx=(0, 16))
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags1, text='Force (ignore manifest)',
                        variable=self.force_var).pack(side=tk.LEFT)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, pady=4)
        self.run_btn = ttk.Button(btn_row, text='Apply All', width=12)
        self.run_btn.pack(side=tk.LEFT)
        self.exif_btn = ttk.Button(btn_row, text='Run exiftool', width=12)
        self.exif_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.mtime_btn = ttk.Button(btn_row, text='Adapt mtime', width=12)
        self.mtime_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.btime_btn = ttk.Button(btn_row, text='Adapt btime', width=12)
        self.btime_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.cancel_btn = ttk.Button(btn_row, text='Cancel', width=10,
                                     state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT)

        out_frame = ttk.LabelFrame(self, text='Output', padding=4)
        out_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 2))

        self.output = scrolledtext.ScrolledText(
            out_frame, wrap=tk.WORD, font=('Consolas', 10),
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white', height=6)
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.config(state=tk.DISABLED)

        self.status = ttk.Label(self, text='Ready', relief=tk.SUNKEN,
                                anchor=tk.W, padding=(4, 2))
        self.status.pack(fill=tk.X, pady=(4, 0))

    _on_back = lambda self: None

    def set_on_back(self, cb):
        self._on_back = cb

    def set_commands(self, *, apply_all=None, exif=None, mtime=None,
                     btime=None, cancel=None):
        if apply_all:
            self.run_btn.config(command=apply_all)
        if exif:
            self.exif_btn.config(command=exif)
        if mtime:
            self.mtime_btn.config(command=mtime)
        if btime:
            self.btime_btn.config(command=btime)
        if cancel:
            self.cancel_btn.config(command=cancel)

    def log(self, msg):
        self.output.config(state=tk.NORMAL)
        self.output.insert(tk.END, msg + '\n')
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

    def set_status(self, msg):
        self.status.config(text=msg)

    def set_buttons_enabled(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        for btn in (self.run_btn, self.exif_btn, self.mtime_btn,
                     self.btime_btn):
            btn.config(state=state)

    def set_cancel_enabled(self, enabled):
        self.cancel_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def clear_output(self):
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)
