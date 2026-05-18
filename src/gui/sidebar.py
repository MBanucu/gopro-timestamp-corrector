import tkinter as tk
from tkinter import ttk


_STEP_NAMES = {1: 'Select Directory', 2: 'Review & Calibrate', 3: 'Run'}
_CIRCLED = {1: '\u2460', 2: '\u2461', 3: '\u2462'}

_COLORS = {
    'active_bg': '#cce5ff',
    'active_fg': '#004080',
    'completed_bg': '#d4edda',
    'completed_fg': '#155724',
    'upcoming_fg': '#bbb',
    'upcoming_icon': '#ccc',
    'separator': '#e0e0e0',
}


class Sidebar(ttk.Frame):
    def __init__(self, parent, *, on_step_click=None, on_history=None, **kw):
        super().__init__(parent, width=220, **kw)
        self.pack_propagate(False)
        self._on_step_click = on_step_click
        self._on_history = on_history

        self._rows: dict[int, tuple[tk.Frame, tk.Label, tk.Label]] = {}

        self._build()

    def _build(self):
        hdr = tk.Label(self, text='\u23f1 Corrections', font=('', 11, 'bold'),
                       bg='#f5f5f5', fg='#333', anchor=tk.W, padx=14, pady=12)
        hdr.pack(fill=tk.X)

        sep = tk.Frame(self, height=1, bg=_COLORS['separator'])
        sep.pack(fill=tk.X, padx=8)

        for n in range(1, 4):
            row = tk.Frame(self, bg='#f5f5f5', cursor='hand2', padx=14, pady=8)
            row.pack(fill=tk.X)

            icon = tk.Label(row, text=_CIRCLED[n], bg='#f5f5f5',
                            fg=_COLORS['upcoming_icon'], font=('', 13), width=2, anchor=tk.CENTER)
            icon.pack(side=tk.LEFT)

            label = tk.Label(row, text=_STEP_NAMES[n], bg='#f5f5f5',
                             fg=_COLORS['upcoming_fg'], font=('', 10), anchor=tk.W)
            label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

            self._rows[n] = (row, icon, label)

            for w in (row, icon, label):
                w.bind('<Button-1>', lambda e, nn=n: self._on_step_click(nn) if self._on_step_click else None)

            sep = tk.Frame(self, height=1, bg=_COLORS['separator'])
            sep.pack(fill=tk.X, padx=8)

        # Spacer
        tk.Frame(self, bg='#f5f5f5').pack(fill=tk.BOTH, expand=True)

        # History button
        hist_btn = ttk.Button(self, text='\U0001f4cb History',
                              command=self._on_history if self._on_history else lambda: None)
        hist_btn.pack(fill=tk.X, padx=14, pady=(0, 10))

    def update_steps(self, current: int, completed: list[bool]):
        for n in range(1, 4):
            row, icon, label = self._rows[n]
            is_current = n == current
            is_done = completed[n] if n < len(completed) else False

            if is_current:
                row.configure(bg=_COLORS['active_bg'])
                icon.configure(bg=_COLORS['active_bg'], fg=_COLORS['active_fg'],
                               text='\u25b6')
                label.configure(bg=_COLORS['active_bg'], fg=_COLORS['active_fg'])
            elif is_done:
                row.configure(bg=_COLORS['completed_bg'])
                icon.configure(bg=_COLORS['completed_bg'], fg=_COLORS['completed_fg'],
                               text='\u2713')
                label.configure(bg=_COLORS['completed_bg'], fg=_COLORS['completed_fg'])
            else:
                row.configure(bg='#f5f5f5')
                icon.configure(bg='#f5f5f5', fg=_COLORS['upcoming_icon'],
                               text=_CIRCLED[n])
                label.configure(bg='#f5f5f5', fg=_COLORS['upcoming_fg'])
