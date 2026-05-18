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
        ttk.Entry(row, textvariable=self.dir_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row, text='Browse...', command=self.browse_dir,
                   width=10).pack(side=tk.RIGHT, padx=(0, 2))
        self.detect_btn = ttk.Button(row, text='Detect', width=8,
                                      command=self.detect_gopro)
        self.detect_btn.pack(side=tk.RIGHT, padx=(0, 2))
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

    set_cal_data = lambda self, data: None

    def set_on_set_cal_data(self, cb):
        self.set_cal_data = cb

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

    def detect_gopro(self):
        import scanner
        self._set_status('Scanning for GoPro devices...')
        paths = scanner.find_gopro_paths()
        if not paths:
            self._set_status('Ready')
            messagebox.showinfo('No GoPro Found',
                                'No GoPro media directories found on any '
                                'mounted device.')
            return
        if len(paths) == 1:
            self._select_gopro_path(paths[0])
            return
        self._show_path_selector(paths)

    def _select_gopro_path(self, path: Path):
        self.dir_var.set(str(path))
        self._summary_var.set('')
        self.cal_bar.auto(str(path))
        self._set_status('Ready')

    def _show_path_selector(self, paths: list[Path]):
        win = tk.Toplevel(self)
        win.title('Select GoPro Directory')
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text='Multiple GoPro directories found:',
                  padding=(10, 10, 10, 0)).pack(fill=tk.X)
        lb = tk.Listbox(win, width=80, height=min(len(paths), 10))
        lb.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        for p in paths:
            lb.insert(tk.END, str(p))
        lb.selection_set(0)
        lb.focus_set()

        def confirm():
            sel = lb.curselection()
            if sel:
                self._select_gopro_path(paths[sel[0]])
            win.destroy()

        def cancel():
            win.destroy()
            self._set_status('Ready')

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        ttk.Button(btn_frame, text='OK', command=confirm).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_frame, text='Cancel', command=cancel).pack(side=tk.LEFT)
        lb.bind('<Double-Button-1>', lambda e: confirm())
        win.wait_window()

    def clear_summary(self):
        self._summary_var.set('')
