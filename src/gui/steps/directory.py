import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from gui.cal_file import CalibrationFileBar


class StepDirectory(ttk.Frame):
    def __init__(self, parent, *, on_analyzed=None, log_fn=None,
                 set_status_fn=None, **kw):
        super().__init__(parent, **kw)
        self._on_analyzed = on_analyzed
        self._log = log_fn or (lambda m: None)
        self._set_status = set_status_fn or (lambda m: None)

        ttk.Label(self, text='1. Select Directory',
                  font=('', 13, 'bold')).pack(anchor=tk.W, pady=(0, 8))

        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text='Directory:', width=14).pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=str(Path.cwd()))
        self.dir_combo = ttk.Combobox(row, textvariable=self.dir_var,
                                      postcommand=self._refresh_detected)
        self.dir_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.dir_combo.bind('<<ComboboxSelected>>', self._on_combo_select)
        ttk.Button(row, text='Browse...', command=self.browse_dir,
                   width=10).pack(side=tk.RIGHT, padx=(0, 2))
        self.analyze_btn = ttk.Button(row, text='Analyze',
                                       command=self.analyze_files, width=10)
        self.analyze_btn.pack(side=tk.RIGHT)

        self.cal_bar = CalibrationFileBar(self, on_set_data=self.set_cal_data,
                                          log_fn=self._log)
        self.cal_bar.pack(fill=tk.X, pady=(8, 4))

        self._summary_var = tk.StringVar()
        self._summary_label = ttk.Label(self, textvariable=self._summary_var,
                                        foreground='#555', font=('', 9))
        self._summary_label.pack(anchor=tk.W, pady=(4, 0))

        self._refresh_detected()

    set_cal_data = lambda self, data: None

    def set_on_set_cal_data(self, cb):
        self.set_cal_data = cb

    def _refresh_detected(self):
        import scanner
        paths = scanner.find_gopro_paths()
        self.dir_combo['values'] = [str(p) for p in paths]

    def _on_combo_select(self, event=None):
        d = self.dir_var.get()
        if d:
            self._summary_var.set('')
            self.cal_bar.auto(d)

    def analyze_files(self):
        target_dir = self.dir_var.get()
        if not target_dir:
            return
        target = Path(target_dir)
        if not target.is_dir():
            messagebox.showerror('Error', 'Directory does not exist.')
            return

        import media
        if not media.exiftool_available():
            messagebox.showerror('Error', 'exiftool not found.')
            return

        self._set_status('Analyzing files...')

        import analysis as an_mod
        try:
            result = an_mod.analyze(target)
            if result.total_files == 0:
                messagebox.showinfo('Analysis', 'No media files found.')
                self._set_status('Ready')
                return
            self._summary_var.set(
                f'{len(result.sets)} sets, {result.total_files} files found')
            self._log(f'Analysis: {len(result.sets)} file sets, '
                      f'{result.total_files} files')
            self._set_status('Ready')
            if self._on_analyzed:
                self._on_analyzed(result)
        except Exception as e:
            messagebox.showerror('Analysis Error', str(e))
            self._set_status('Error during analysis')

    def browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)
            self._summary_var.set('')
            self.cal_bar.auto(d)

    def clear_summary(self):
        self._summary_var.set('')
