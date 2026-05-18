import tkinter as tk
from tkinter import ttk

import btime


# Human-readable labels for the btime method listbox.
_BTIME_LABELS = {
    'exfat_raw': 'exFAT raw block',
    'debugfs': 'debugfs (ext4)',
    'fuse': 'FUSE + faketime (exFAT)',
    'clock': 'System clock',
}

# All methods that can appear in the priority list.
_BTIME_METHODS = tuple(_BTIME_LABELS)


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
        self._back_link = back_link

        opt = ttk.LabelFrame(self, text='Corrections', padding=8)
        opt.pack(fill=tk.X, pady=(0, 6))

        self.fix_embedded_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text='EXIF / QuickTime metadata',
                        variable=self.fix_embedded_var).pack(anchor=tk.W,
                                                             pady=1)

        self.fix_mtime_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text='Filesystem modification time (mtime)',
                        variable=self.fix_mtime_var).pack(anchor=tk.W, pady=1)

        # ── Birth time (btime) with reorderable priority list ─────────
        self.fix_btime_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text='Filesystem birth time (btime)',
                        variable=self.fix_btime_var,
                        command=self._toggle_btime).pack(anchor=tk.W, pady=1)

        btime_panel = ttk.Frame(opt)
        btime_panel.pack(fill=tk.X, padx=(24, 0), pady=(2, 0))
        self._btime_panel = btime_panel

        list_side = ttk.Frame(btime_panel)
        list_side.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._btime_list = tk.Listbox(list_side, height=5, exportselection=False,
                                      selectmode=tk.SINGLE, font=('', 9))
        self._btime_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scroll = ttk.Scrollbar(list_side, orient=tk.VERTICAL,
                               command=self._btime_list.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._btime_list.config(yscrollcommand=scroll.set)

        btn_col = ttk.Frame(btime_panel)
        btn_col.pack(side=tk.LEFT, padx=(6, 0))
        self._btn_up = ttk.Button(btn_col, text='\u25b2', width=3,
                                  command=self._move_up)
        self._btn_up.pack()
        self._btn_down = ttk.Button(btn_col, text='\u25bc', width=3,
                                    command=self._move_down)
        self._btn_down.pack()
        self._btn_add = ttk.Button(btn_col, text='+', width=3,
                                   command=self._add_method)
        self._btn_add.pack(pady=(4, 0))
        self._btn_remove = ttk.Button(btn_col, text='\u2715', width=3,
                                      command=self._remove_method)
        self._btn_remove.pack()

        ttk.Label(btime_panel,
                  text='Tries each method in order. The first that succeeds is used.',
                  foreground='gray', font=('', 8)).pack(side=tk.LEFT,
                                                        padx=(12, 0))

        # Initialise with a conservative default (clock only).
        # set_filesystem() expands to compatible methods once the fs is known.
        self._btime_methods = ['clock']
        self._compatible_methods = list(_BTIME_METHODS)
        self._rebuild_listbox()
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
        self.next_btn = ttk.Button(nav, text='Proceed to Run \u2192',
                                    command=self._on_next)
        self.next_btn.pack(side=tk.RIGHT)

    # ── Step wiring ──────────────────────────────────────────────────

    def set_on_back(self, cb):
        self._on_back = cb

    def set_on_next(self, cb):
        self._on_next = cb
        self.next_btn.config(command=cb)

    # ── Btime list management ─────────────────────────────────────────

    def _btime_widgets(self):
        return (self._btime_list, self._btn_up, self._btn_down,
                self._btn_add, self._btn_remove)

    def _toggle_btime(self):
        state = tk.NORMAL if self.fix_btime_var.get() else tk.DISABLED
        for w in self._btime_widgets():
            w.config(state=state)

    def set_filesystem(self, fs_type: str | None):
        """Filter btime methods to only those compatible with *fs_type*.

        Call after the target directory is known (post‑analysis).
        Pass ``None`` when detection fails — only ``auto`` and ``clock``
        are shown (the add button still offers all methods).
        """
        if fs_type is None:
            self._compatible_methods = list(_BTIME_METHODS)
            self._btime_methods = [m for m in ('clock',)
                                   if m in self._btime_methods] or ['clock']
        else:
            self._compatible_methods = list(btime.compatible_methods(fs_type))
            self._btime_methods = list(self._compatible_methods)
        self._rebuild_listbox()

    def _rebuild_listbox(self):
        state = self._btime_list.cget('state')
        self._btime_list.config(state=tk.NORMAL)
        self._btime_list.delete(0, tk.END)
        for method in self._btime_methods:
            self._btime_list.insert(tk.END, _BTIME_LABELS.get(method, method))
        self._btime_list.config(state=state)

    def _move_up(self):
        sel = self._btime_list.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self._btime_methods[i], self._btime_methods[i - 1] = \
            self._btime_methods[i - 1], self._btime_methods[i]
        self._rebuild_listbox()
        self._btime_list.selection_set(i - 1)

    def _move_down(self):
        sel = self._btime_list.curselection()
        if not sel or sel[0] >= len(self._btime_methods) - 1:
            return
        i = sel[0]
        self._btime_methods[i], self._btime_methods[i + 1] = \
            self._btime_methods[i + 1], self._btime_methods[i]
        self._rebuild_listbox()
        self._btime_list.selection_set(i + 1)

    def _add_method(self):
        existing = set(self._btime_methods)
        avail = [m for m in self._compatible_methods if m not in existing]
        if not avail:
            return
        menu = tk.Menu(self._btime_list, tearoff=0)
        for m in avail:
            label = _BTIME_LABELS.get(m, m)
            menu.add_command(label=label, command=lambda m=m: self._do_add(m))
        menu.post(self._btn_add.winfo_rootx(), self._btn_add.winfo_rooty())

    def _do_add(self, method):
        self._btime_methods.append(method)
        self._rebuild_listbox()

    def _remove_method(self):
        sel = self._btime_list.curselection()
        if not sel:
            return
        i = sel[0]
        del self._btime_methods[i]
        self._rebuild_listbox()

    # ── Options accessor ──────────────────────────────────────────────

    def get_options(self) -> dict:
        return {
            'fix_embedded': self.fix_embedded_var.get(),
            'fix_mtime': self.fix_mtime_var.get(),
            'fix_btime': list(self._btime_methods)
                         if self.fix_btime_var.get()
                         else 'off',
            'dry_run': self.dry_run_var.get(),
            'force': self.force_var.get(),
        }
