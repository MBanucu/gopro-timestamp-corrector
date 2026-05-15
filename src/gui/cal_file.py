"""Calibration file management bar for the GUI."""

from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import calibration


class CalibrationFileBar(ttk.Frame):
    """A row with file path entry + Load/Save/Auto buttons for calibration files."""

    def __init__(self, parent, on_set_data, log_fn, **kw):
        super().__init__(parent, **kw)
        self._on_set_data = on_set_data
        self._log = log_fn
        self.cal_data = calibration.default()
        self.cal_var = tk.StringVar()

        ttk.Label(self, text='Calibration:', width=14).pack(side=tk.LEFT)
        ttk.Entry(self, textvariable=self.cal_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(self, text='Load...', command=self.load,
                   width=8).pack(side=tk.RIGHT, padx=(0, 2))
        ttk.Button(self, text='Save...', command=self.save,
                   width=8).pack(side=tk.RIGHT, padx=(0, 2))
        ttk.Button(self, text='Auto', command=self.auto,
                   width=6).pack(side=tk.RIGHT)

    def load_default(self, data=None):
        if data is not None:
            self.cal_data = data
        else:
            self.cal_data = calibration.default()
        self.cal_var.set('')
        self._on_set_data(self.cal_data)

    def get_data(self):
        return self.cal_data

    def set_data(self, data):
        self.cal_data = data
        self._on_set_data(data)

    def get_path(self):
        return self.cal_var.get().strip()

    def _update(self, data, path):
        self.cal_data = data
        self.cal_var.set(path)
        self._on_set_data(data)

    def load(self):
        path = filedialog.askopenfilename(
            title='Load calibration',
            filetypes=[('JSON', '*.json'), ('Calibration', '*.txt'), ('All', '*')],
            initialdir=None,
        )
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext == '.json':
                data = calibration.load_json(path)
            else:
                data = calibration.from_text(path)
            self._update(data, path)
            self._log(f'Loaded: {Path(path).name}')
        except Exception as e:
            messagebox.showerror('Load error', str(e))

    def save(self):
        path = filedialog.asksaveasfilename(
            title='Save calibration',
            defaultextension='.json',
            filetypes=[('JSON', '*.json'), ('All', '*')],
            initialdir=None,
        )
        if not path:
            return
        try:
            data = self.get_data()
            if Path(path).suffix.lower() == '.json':
                calibration.save_json(path, data)
            else:
                Path(path).write_text(calibration.to_text(data))
            self.cal_var.set(path)
            self._log(f'Saved: {Path(path).name}')
        except Exception as e:
            messagebox.showerror('Save error', str(e))

    def auto(self, search_dir):
        p = Path(search_dir)
        for f in p.glob('*time translation*'):
            try:
                data = calibration.from_text(f)
                self._update(data, str(f))
                self._log(f'Auto-loaded: {f.name}')
                return
            except Exception:
                continue
        for f in p.glob('*.json'):
            try:
                data = calibration.load_json(f)
                self._update(data, str(f))
                self._log(f'Auto-loaded: {f.name}')
                return
            except Exception:
                continue
        self._log('No calibration file found')
