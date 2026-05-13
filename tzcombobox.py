try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = ttk = None


class FilteringCombobox(ttk.Frame):
    """An Entry with a searchable dropdown listbox. Full focus control."""

    def __init__(self, parent, all_values=None, **kw):
        self._all = all_values or []
        width = kw.pop('width', 35)
        textvariable = kw.pop('textvariable', None)
        super().__init__(parent)
        self._var = textvariable or tk.StringVar()
        self._popup = None

        self.entry = ttk.Entry(self, textvariable=self._var, width=width)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn = ttk.Label(self, text='▼', font=('', 8),
                              anchor=tk.CENTER, width=3, relief=tk.RAISED)
        self.btn.pack(side=tk.RIGHT, fill=tk.Y)
        self.btn.bind('<Button-1>', lambda e: self.toggle_popup())

        self.entry.bind('<KeyRelease>', self._on_key)
        self.entry.bind('<Tab>', self._on_tab)
        self.entry.bind('<FocusOut>', lambda e: self.after(200, self._close_popup))
        self.entry.bind('<Return>', self._pick_selected)
        self.entry.bind('<Down>', lambda e: self._select_next())
        self.entry.bind('<Up>', lambda e: self._select_prev())
        self.entry.bind('<Escape>', lambda e: self._close_popup())
        self.entry.bind('<FocusIn>', self._on_focus_in)
        self.entry.bind('<Button-1>', self._on_click_entry)

        self._popup_listbox = None
        self._sel_index = 0
        self._filtered = []

    def _on_focus_in(self, event):
        self._filtered = list(self._all)
        self.after(30, self._show_popup)
        try:
            self.entry.selection_range(0, tk.END)
        except tk.TclError:
            pass

    def _on_click_entry(self, event):
        self._filtered = list(self._all)
        self.after(10, self._show_popup)

    def toggle_popup(self):
        if self._popup and self._popup.winfo_ismapped():
            self._close_popup()
        else:
            self._filtered = list(self._all)
            self._show_popup()

    def _show_popup(self):
        self._close_popup()
        if not self._filtered:
            return
        self._popup = tk.Toplevel(self)
        self._popup.withdraw()
        self._popup.overrideredirect(True)
        self._popup.transient(self.winfo_toplevel())

        lb = tk.Listbox(self._popup, height=min(len(self._filtered), 12), width=38,
                        font=self.entry.cget('font'), exportselection=False,
                        highlightthickness=0, borderwidth=1, relief=tk.SOLID)
        lb.pack(fill=tk.BOTH, expand=True)
        self._popup_listbox = lb

        for v in self._filtered:
            lb.insert(tk.END, v)
        lb.bind('<ButtonRelease-1>', self._pick_clicked)
        lb.bind('<Double-Button-1>', self._pick_clicked)
        lb.bind('<Return>', self._pick_selected)
        lb.bind('<Escape>', lambda e: self._close_popup())
        lb.bind('<Down>', lambda e: self._select_next())
        lb.bind('<Up>', lambda e: self._select_prev())

        self.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height()
        self._popup.geometry(f'+{x}+{y}')
        self._popup.deiconify()
        self._popup.lift()
        self.entry.focus_set()

    def _close_popup(self):
        if self._popup:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None
            self._popup_listbox = None

    def _on_key(self, event):
        if event.keysym in ('Up', 'Down', 'Left', 'Right', 'Return', 'Escape', 'Tab',
                            'Shift_L', 'Shift_R', 'Control_L', 'Control_R',
                            'Alt_L', 'Alt_R', 'Meta_L', 'Meta_R', 'Caps_Lock'):
            return
        if event.keysym in ('BackSpace', 'Delete'):
            self._do_filter()
            return
        # Skip stale KeyRelease events for keys whose character doesn't
        # match the end of the current entry text.  This handles the case
        # where the user presses 'u', then 'r', then releases 'r'
        # (autocomplete fires and changes the text), then releases 'u'.
        text = self.entry.get()
        k = event.keysym
        if len(k) == 1 and text and not text.lower().endswith(k.lower()):
            return
        self._do_autocomplete()

    def _on_tab(self, event):
        """Tab accepts suggestion (clears selection) or moves to next field."""
        try:
            self.entry.index(tk.SEL_FIRST)
            # Selection exists → accept suggestion, stay in field
            self.entry.selection_clear()
            self.entry.icursor(tk.END)
            return 'break'
        except tk.TclError:
            # No selection → normal Tab behavior (move to next widget)
            pass

    def _common_prefix(self, matches, typed):
        if not matches:
            return typed
        if len(matches) == 1:
            return matches[0]
        low_typed = typed.lower()
        prefix = typed
        for i in range(len(typed), len(matches[0])):
            ci = matches[0][i].lower()
            if all(len(m) > i and m[i].lower() == ci for m in matches):
                prefix += matches[0][i]
            else:
                break
        return prefix

    def _do_autocomplete(self):
        full = self.entry.get()
        typed = full.strip()
        if not typed:
            self._filtered = list(self._all)
            self._relist()
            return

        lower = typed.lower()
        self._filtered = [z for z in self._all if lower in z.lower()]

        first = next((m for m in self._filtered if m.lower().startswith(lower)), None)
        if first and first != full:
            completion = self._common_prefix(self._filtered, typed)
            if completion == full:
                completion = first
            self._relist()
            try:
                e = str(self.entry)
                self.tk.eval(f'{e} delete 0 end')
                self.tk.eval(f'{e} insert 0 {{{completion}}}')
                self.tk.eval(f'{e} selection range {len(typed)} end')
                self.tk.eval(f'{e} icursor {len(typed)}')
            except tk.TclError:
                pass
            return
        self._relist()

    def _do_filter(self):
        full = self.entry.get()
        typed = full.strip()
        if not typed:
            self._filtered = list(self._all)
        else:
            lower = typed.lower()
            self._filtered = [z for z in self._all if lower in z.lower()]
        self._relist()

    def _relist(self):
        if not self._popup:
            self._show_popup()
            return
        lb = self._popup_listbox
        if not lb:
            return
        lb.delete(0, tk.END)
        for v in self._filtered:
            lb.insert(tk.END, v)
        if self._filtered:
            self._sel_index = 0
            lb.selection_clear(0, tk.END)
            lb.selection_set(0)
            lb.see(0)

    def _pick_selected(self, event=None):
        if self._popup_listbox and self._filtered:
            idx = self._sel_index
            if 0 <= idx < len(self._filtered):
                self._var.set(self._filtered[idx])
        self._close_popup()
        self.entry.focus_set()
        self.entry.icursor(tk.END)

    def _pick_clicked(self, event):
        lb = self._popup_listbox
        if not lb:
            return
        sel = lb.curselection()
        if sel:
            self._var.set(self._filtered[sel[0]])
        self._close_popup()
        self.entry.focus_set()
        self.entry.icursor(tk.END)

    def _select_next(self):
        if not self._filtered:
            return
        self._sel_index = (self._sel_index + 1) % len(self._filtered)
        lb = self._popup_listbox
        if lb:
            lb.selection_clear(0, tk.END)
            lb.selection_set(self._sel_index)
            lb.see(self._sel_index)
        return 'break'

    def _select_prev(self):
        if not self._filtered:
            return
        self._sel_index = (self._sel_index - 1) % len(self._filtered)
        lb = self._popup_listbox
        if lb:
            lb.selection_clear(0, tk.END)
            lb.selection_set(self._sel_index)
            lb.see(self._sel_index)
        return 'break'

    def get(self):
        return self.entry.get()

    def set(self, value):
        self._var.set(value)

    def set_values(self, new_all):
        self._all = list(new_all)
        self._filtered = list(new_all)
