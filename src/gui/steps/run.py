import tkinter as tk
from tkinter import ttk

from plan import Instruction


_STATUS_ICONS = {
    'pending': '\u25cb',   # ○
    'running': '\u25b6',   # ▶
    'done':    '\u2713',   # ✓
    'failed':  '\u2717',   # ✗
    'skipped': '\u2013',   # –
}


class StepRun(ttk.Frame):
    """Run step displaying an instruction tree and progress feedback."""

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

        # ── Instruction tree ──────────────────────────────────
        tree_frame = ttk.LabelFrame(self, text='Execution plan', padding=4)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        columns = ('status', 'instruction')
        self.tree = ttk.Treeview(tree_frame, columns=columns, show='headings',
                                 height=8, selectmode='none')
        self.tree.heading('status', text='')
        self.tree.column('status', width=36, anchor=tk.CENTER, stretch=False)
        self.tree.heading('instruction', text='Step')
        self.tree.column('instruction', width=700)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                            command=self.tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=vsb.set)

        self._instruction_items: list[str] = []  # iid per instruction

        # ── Buttons ────────────────────────────────────────────
        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, pady=4)
        self.apply_btn = ttk.Button(btn_row, text='Apply', width=12)
        self.apply_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btn_row, text='Cancel', width=10,
                                     state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT)

    _on_back = lambda self: None  # overridden by set_on_back

    def set_on_back(self, cb):
        self._on_back = cb

    def set_commands(self, *, apply=None, cancel=None):
        if apply:
            self.apply_btn.config(command=apply)
        if cancel:
            self.cancel_btn.config(command=cancel)

    # ── Instruction management ───────────────────────────────────

    def set_instructions(self, instructions: list[Instruction]):
        """Populate the tree from a list of *Instruction* objects."""
        self._instruction_items = []
        for item in self.tree.get_children():
            self.tree.delete(item)
        for inst in instructions:
            icon = _STATUS_ICONS.get(inst.status, '?')
            item_id = self.tree.insert('', tk.END,
                                       values=(icon, inst.label))
            self._instruction_items.append(item_id)

    def update_instruction(self, index: int, status: str):
        """Update the status icon of the instruction at *index*."""
        item = self._instruction_items[index]
        icon = _STATUS_ICONS.get(status, '?')
        self.tree.set(item, column='status', value=icon)
        self.tree.set(item, column='instruction',
                      value=self.tree.set(item, 'instruction'))

    # ── Button state ────────────────────────────────────────────

    def set_buttons_enabled(self, enabled):
        self.apply_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def set_cancel_enabled(self, enabled):
        self.cancel_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)
