import json
from pathlib import Path

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox


HISTORY_DIR_NAME = '.timestamp_correction_history'


class HistoryViewer(tk.Toplevel):
    def __init__(self, parent, target_dir):
        super().__init__(parent)
        self.title('Correction History')
        self.geometry('950x650')

        self.target_dir = Path(target_dir)
        self.history_dir = self.target_dir / HISTORY_DIR_NAME

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Left: run list
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        ttk.Label(left, text='Past Runs', font=('', 10, 'bold')).pack(
            anchor=tk.W, pady=(0, 4))

        cols = ('date', 'written', 'errors')
        self.run_tree = ttk.Treeview(left, columns=cols, show='headings',
                                     height=20)
        self.run_tree.heading('date', text='Date')
        self.run_tree.heading('written', text='Files')
        self.run_tree.heading('errors', text='Errors')
        self.run_tree.column('date', width=200, minwidth=120)
        self.run_tree.column('written', width=60, minwidth=50, anchor=tk.CENTER)
        self.run_tree.column('errors', width=60, minwidth=50, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(left, orient=tk.VERTICAL,
                            command=self.run_tree.yview)
        self.run_tree.configure(yscrollcommand=vsb.set)
        self.run_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.run_tree.bind('<<TreeviewSelect>>', self._on_select_run)

        # Right: metadata + actions
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        ttk.Label(right, text='Run Details', font=('', 10, 'bold')).pack(
            anchor=tk.W, pady=(0, 4))

        self.meta_text = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, font=('Consolas', 10), height=15)
        self.meta_text.pack(fill=tk.BOTH, expand=True)

        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self.diff_btn = ttk.Button(btn_row, text='Show Diff',
                                   command=self._show_diff, state=tk.DISABLED)
        self.diff_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.open_dir_btn = ttk.Button(btn_row, text='Open Run Folder',
                                       command=self._open_run_dir,
                                       state=tk.DISABLED)
        self.open_dir_btn.pack(side=tk.LEFT)

        self._selected_run_id = None
        self._load_runs()

    def _load_runs(self):
        if not self.history_dir.is_dir():
            self.meta_text.insert(tk.END, 'No history found.\n')
            return
        for d in sorted(self.history_dir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            run_json = d / 'run.json'
            if not run_json.exists():
                continue
            meta = json.loads(run_json.read_text())
            ts = meta.get('timestamp', d.name)
            summary = meta.get('summary', {})
            written = summary.get('written', '?')
            errors = len(summary.get('errors', []))
            self.run_tree.insert('', tk.END, iid=d.name,
                                 values=(ts, str(written), str(errors)))

    def _on_select_run(self, event):
        sel = self.run_tree.selection()
        if not sel:
            return
        run_id = sel[0]
        self._selected_run_id = run_id
        run_json = self.history_dir / run_id / 'run.json'
        if run_json.exists():
            self.meta_text.delete(1.0, tk.END)
            self.meta_text.insert(tk.END,
                                  json.dumps(json.loads(run_json.read_text()),
                                             indent=2))
        self.diff_btn.config(state=tk.NORMAL)
        self.open_dir_btn.config(state=tk.NORMAL)

    def _show_diff(self):
        if not self._selected_run_id:
            return
        before = self.history_dir / self._selected_run_id / 'before.json'
        after = self.history_dir / self._selected_run_id / 'after.json'
        if not before.exists() or not after.exists():
            messagebox.showinfo('Diff', 'No before/after data for this run.')
            return
        DiffViewer(self, before, after)

    def _open_run_dir(self):
        if self._selected_run_id:
            run_dir = self.history_dir / self._selected_run_id
            import subprocess
            subprocess.Popen(['xdg-open', str(run_dir)])


class DiffViewer(tk.Toplevel):
    def __init__(self, parent, before_path, after_path):
        super().__init__(parent)
        self.title('Before / After Diff')
        self.geometry('1100x750')

        before_data = json.loads(before_path.read_text())
        after_data = json.loads(after_path.read_text())

        # Incorporate btime data if available
        btime_before = self._load_btimes(
            before_path.parent / 'btimes_before.json')
        btime_after = self._load_btimes(
            before_path.parent / 'btimes_after.json')

        self.before_map = {}
        for f in before_data:
            sf = f.get('SourceFile', '')
            self.before_map[sf] = f
        if btime_before:
            for sf, bt in btime_before.items():
                if sf in self.before_map and bt is not None:
                    self.before_map[sf]['File:FileBirthDate'] = bt
        self.after_map = {}
        for f in after_data:
            sf = f.get('SourceFile', '')
            self.after_map[sf] = f
        if btime_after:
            for sf, bt in btime_after.items():
                if sf in self.after_map and bt is not None:
                    self.after_map[sf]['File:FileBirthDate'] = bt

        all_files = sorted(set(self.before_map) | set(self.after_map))
        self.file_list = all_files

        # File selector
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(top, text='File:').pack(side=tk.LEFT, padx=(0, 4))
        self.file_var = tk.StringVar()
        self.file_combo = ttk.Combobox(top, textvariable=self.file_var,
                                       values=self.file_list, width=80)
        self.file_combo.pack(side=tk.LEFT, padx=4, fill=tk.X, expand=True)
        self.file_combo.bind('<<ComboboxSelected>>', self._render_diff)

        # Navigation: prev/next changed file
        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, padx=6, pady=(2, 4))
        self.nav_label = ttk.Label(nav, foreground='#555', font=('', 9))
        self.nav_label.pack(side=tk.LEFT)
        ttk.Button(nav, text='\u25b2 Prev Changed',
                   command=self._prev_changed, width=14).pack(side=tk.RIGHT,
                                                              padx=(2, 0))
        ttk.Button(nav, text='Next Changed \u25bc',
                   command=self._next_changed, width=14).pack(side=tk.RIGHT)

        # Side-by-side panes
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        left_frame = ttk.LabelFrame(paned, text='Before', padding=2)
        self.before_text = scrolledtext.ScrolledText(
            left_frame, wrap=tk.NONE, font=('Consolas', 10),
            bg='#fcfcfc', fg='#333')
        self.before_text.pack(fill=tk.BOTH, expand=True)
        paned.add(left_frame, weight=1)

        right_frame = ttk.LabelFrame(paned, text='After', padding=2)
        self.after_text = scrolledtext.ScrolledText(
            right_frame, wrap=tk.NONE, font=('Consolas', 10),
            bg='#fcfcfc', fg='#333')
        self.after_text.pack(fill=tk.BOTH, expand=True)
        paned.add(right_frame, weight=1)

        # Tags for coloring
        for t in ('before', 'after'):
            self.before_text.tag_configure(
                'removed', background='#fdd', foreground='#a00')
            self.before_text.tag_configure(
                'changed', background='#fee', foreground='#c00')
            self.before_text.tag_configure(
                'same', foreground='#999')
            self.before_text.tag_configure(
                'key', foreground='#0066cc')
            self.before_text.tag_configure(
                'header', font=('Consolas', 10, 'bold'))
            self.after_text.tag_configure(
                'added', background='#dfd', foreground='#0a0')
            self.after_text.tag_configure(
                'changed', background='#efe', foreground='#080')
            self.after_text.tag_configure(
                'same', foreground='#999')
            self.after_text.tag_configure(
                'key', foreground='#0066cc')
            self.after_text.tag_configure(
                'header', font=('Consolas', 10, 'bold'))

        self._changed_indices: list[int] = []
        self._current_file_idx = 0

        if self.file_list:
            self.file_var.set(self.file_list[0])
            self._render_diff()

    @staticmethod
    def _load_btimes(path: Path) -> dict[str, str | None]:
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except Exception:
            return {}

    def _render_diff(self, event=None):
        fname = self.file_var.get()
        if not fname:
            return
        self._current_file_idx = self.file_list.index(fname)

        before = self.before_map.get(fname, {})
        after = self.after_map.get(fname, {})

        self.before_text.delete(1.0, tk.END)
        self.after_text.delete(1.0, tk.END)

        if not before and after:
            self.before_text.insert(tk.END, '(file not present before)\n',
                                    'removed')
            self._render_file(self.after_text, after, 'added')
            self._changed_indices = [self._current_file_idx]
            self._update_nav()
            return

        if before and not after:
            self._render_file(self.before_text, before, 'removed')
            self.after_text.insert(tk.END, '(file not present after)\n',
                                   'added')
            self._changed_indices = [self._current_file_idx]
            self._update_nav()
            return

        keys = self._collect_keys(before, after)
        has_changes = False
        for key in keys:
            b_val = before.get(key, '<MISSING>')
            a_val = after.get(key, '<MISSING>')
            changed = b_val != a_val
            if changed and key not in self._skip_keys():
                has_changes = True

            b_tag = 'changed' if changed else 'same'
            a_tag = 'changed' if changed else 'same'

            b_line = f'{key}: {b_val}'
            a_line = f'{key}: {a_val}'

            self.before_text.insert(tk.END, b_line + '\n', ('key' if changed else b_tag))
            self.after_text.insert(tk.END, a_line + '\n', ('key' if changed else a_tag))

        self._changed_indices = ([self._current_file_idx]
                                 if has_changes else [])
        self._update_nav()

    def _collect_keys(self, before, after):
        keys = set()
        keys.update(before.keys())
        keys.update(after.keys())
        excluded = self._skip_keys()
        return sorted(k for k in keys if k not in excluded)

    def _skip_keys(self):
        return {
            'SourceFile', 'FileName', 'Directory',
            'File:FileName', 'File:Directory',
            'File:FilePermissions', 'File:FileSize',
        }

    def _render_file(self, widget, data, tag):
        for k, v in sorted(data.items()):
            if k in self._skip_keys():
                continue
            widget.insert(tk.END, f'{k}: {v}\n', ('key' if tag == 'added' else tag))

    def _update_nav(self):
        n = len(self._changed_indices)
        if n:
            pos = self._changed_indices.index(self._current_file_idx) + 1
            self.nav_label.config(text=f'{n} file(s) with changes')
        else:
            self.nav_label.config(text='No changes in this file')

    def _next_changed(self):
        if not self._changed_indices:
            return
        cur = self._current_file_idx
        for idx in self._changed_indices:
            if idx > cur:
                self.file_var.set(self.file_list[idx])
                self._render_diff()
                return
        # wrap around
        self.file_var.set(self.file_list[self._changed_indices[0]])
        self._render_diff()

    def _prev_changed(self):
        if not self._changed_indices:
            return
        cur = self._current_file_idx
        rev = list(reversed(self._changed_indices))
        for idx in rev:
            if idx < cur:
                self.file_var.set(self.file_list[idx])
                self._render_diff()
                return
        # wrap around
        self.file_var.set(self.file_list[rev[0]])
        self._render_diff()
