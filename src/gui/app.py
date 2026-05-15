#!/usr/bin/env python3
import json
import subprocess
import sys
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import calibration
from gui.editor import CalibrationEditor, get_all_tz_ids, resolve_tz_abbr
from gui.cal_file import CalibrationFileBar
from gui.file_table import FileSetTable, _fmt_delta
from writer import Writer, WriteJob


SCRIPT_DIR = Path(__file__).resolve().parent


def _compute_delta(actual_editor, gopro_editor):
    actual = actual_editor.get_data()
    gopro = gopro_editor.get_data()
    data = {'actual': actual, 'gopro': gopro}
    ok, *rest = calibration.try_parse(data)
    if ok:
        return rest[0] - rest[1]
    return None


def _parse_delta(text: str) -> timedelta | None:
    """Parse a human-readable timedelta string.

    Accepted formats::

        +2h30m    -1d5h     2:30      -90m      0
    """
    text = text.strip()
    if not text:
        return None

    negative = False
    if text.startswith('-'):
        negative = True
        text = text[1:]
    elif text.startswith('+'):
        text = text[1:]

    if ':' in text:
        parts = text.split(':')
        if len(parts) == 2:
            try:
                h, m = int(parts[0]), int(parts[1])
                d = timedelta(hours=h, minutes=m)
                return -d if negative else d
            except ValueError:
                return None

    import re
    days = hours = minutes = 0
    m = re.search(r'(\d+)d', text)
    if m:
        days = int(m.group(1))
    m = re.search(r'(\d+)h', text)
    if m:
        hours = int(m.group(1))
    m = re.search(r'(\d+)m', text)
    if m:
        minutes = int(m.group(1))

    if days == 0 and hours == 0 and minutes == 0:
        try:
            minutes = int(text)
        except ValueError:
            return None

    d = timedelta(days=days, hours=hours, minutes=minutes)
    return -d if negative else d


class ToolGUI:
    def __init__(self, root):
        self.root = root
        root.title('GoPro Timestamp Corrector')
        root.geometry('1000x800')
        root.minsize(850, 650)

        self.process = None
        self.running = False

        style = ttk.Style()
        style.theme_use('clam')

        main = ttk.Frame(root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # --- Directory ---
        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text='Directory:', width=14).pack(side=tk.LEFT)
        self.dir_var = tk.StringVar(value=str(Path.cwd()))
        ttk.Entry(row, textvariable=self.dir_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row, text='Browse...', command=self.browse_dir, width=10).pack(side=tk.RIGHT, padx=(0, 2))
        self.analyze_btn = ttk.Button(row, text='Analyze', command=self.analyze_files, width=10)
        self.analyze_btn.pack(side=tk.RIGHT)

        # --- Calibration file ---
        self.cal_bar = CalibrationFileBar(main, on_set_data=self.set_cal_data,
                                           log_fn=self.log)
        self.cal_bar.pack(fill=tk.X, pady=4)

        # --- Calibration notebook ---
        self.cal_notebook = ttk.Notebook(main)
        self.cal_notebook.pack(fill=tk.X, pady=4)

        # Tab 1: Calendar
        cal_tab = ttk.Frame(self.cal_notebook, padding=6)
        self.cal_notebook.add(cal_tab, text='Calendar')

        self.actual_editor = CalibrationEditor(cal_tab, 'Actual local time')
        self.actual_editor.grid(row=0, column=0, sticky='ew', padx=(0, 4))

        self.gopro_editor = CalibrationEditor(cal_tab, 'GoPro local time')
        self.gopro_editor.grid(row=0, column=1, sticky='ew')
        cal_tab.columnconfigure(0, weight=1, uniform='editor')
        cal_tab.columnconfigure(1, weight=1, uniform='editor')

        # Tab 2: Delta
        delta_tab = ttk.Frame(self.cal_notebook, padding=6)
        self.cal_notebook.add(delta_tab, text='Delta')
        ttk.Label(delta_tab, text='Enter the time offset directly:',
                  font=('', 8)).pack(anchor=tk.W)
        delta_entry_frame = ttk.Frame(delta_tab)
        delta_entry_frame.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(delta_entry_frame, text='Delta:', foreground='#555').pack(side=tk.LEFT, padx=(0, 4))
        self.delta_entry = ttk.Entry(delta_entry_frame, width=24, font=('', 9))
        self.delta_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.delta_entry.bind('<KeyRelease>', self._on_delta_entry)
        self.delta_entry.bind('<FocusOut>', self._on_delta_entry)
        ttk.Label(delta_tab, text='Examples:  +2h30m   -1d5h   2:30   90m   0',
                  font=('', 7), foreground='#999').pack(anchor=tk.W)

        # --- GPS Button ---
        gps_row = ttk.Frame(main)
        gps_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(gps_row, text='Single GPS...', command=self.auto_gps, width=13).pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Button(gps_row, text='Auto calibrate', command=self.auto_calibrate_from_gps, width=13).pack(side=tk.RIGHT)

        # --- Preview / delta ---
        self.preview_var = tk.StringVar()
        status = ttk.Frame(main)
        status.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(status, textvariable=self.preview_var, foreground='#c33').pack(side=tk.LEFT)

        for ed in (self.actual_editor, self.gopro_editor):
            for var in (ed.date_var, ed.hour_var, ed.min_var, ed.tz_var):
                var.trace_add('write', lambda *a: self.update_preview())

        # --- File Analysis Table ---
        sep = ttk.Separator(main, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=4)

        table_frame = ttk.LabelFrame(main, text='File Analysis', padding=4)
        table_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        self.file_table = FileSetTable(table_frame, manual_delta_changed_cb=self._on_strategy_changed)
        self.file_table.pack(fill=tk.BOTH, expand=True)

        # --- Options ---
        opt = ttk.LabelFrame(main, text='Options', padding=8)
        opt.pack(fill=tk.X, pady=(0, 6))

        btime_row = ttk.Frame(opt)
        btime_row.pack(fill=tk.X, pady=2)
        ttk.Label(btime_row, text='Fix btime:', width=14).pack(side=tk.LEFT)
        self.btime_var = tk.StringVar(value='off')
        bm = ttk.Combobox(btime_row, textvariable=self.btime_var, state='readonly', width=14)
        bm['values'] = ('off', 'auto', 'debugfs', 'fuse', 'clock')
        bm.pack(side=tk.LEFT)
        ttk.Label(btime_row, text='  ext4→debugfs  exFAT→fuse  fallback→clock',
                  foreground='gray').pack(side=tk.LEFT)

        flags1 = ttk.Frame(opt)
        flags1.pack(fill=tk.X, pady=2)
        self.dry_run_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(flags1, text='Dry run', variable=self.dry_run_var).pack(side=tk.LEFT, padx=(0, 16))
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags1, text='Force (ignore manifest)', variable=self.force_var).pack(side=tk.LEFT)

        # --- Run buttons ---
        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=4)
        self.run_btn = ttk.Button(btn_row, text='Apply All', command=self.run_tool, width=12)
        self.run_btn.pack(side=tk.LEFT)
        self.exif_btn = ttk.Button(btn_row, text='Run exiftool', command=self.run_exif, width=12)
        self.exif_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.mtime_btn = ttk.Button(btn_row, text='Adapt mtime', command=self.run_mtime, width=12)
        self.mtime_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.btime_btn = ttk.Button(btn_row, text='Adapt btime', command=self.run_btime, width=12)
        self.btime_btn.pack(side=tk.LEFT, padx=(4, 0))
        self.cancel_btn = ttk.Button(btn_row, text='Cancel', command=self.cancel_run, width=10, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.RIGHT)

        # --- Output ---
        out_frame = ttk.LabelFrame(main, text='Output', padding=4)
        out_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 2))

        self.output = scrolledtext.ScrolledText(out_frame, wrap=tk.WORD, font=('Consolas', 10),
                                                 bg='#1e1e1e', fg='#d4d4d4', insertbackground='white',
                                                 height=6)
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.config(state=tk.DISABLED)

        # --- Status bar ---
        self.status = ttk.Label(main, text='Ready', relief=tk.SUNKEN, anchor=tk.W, padding=(4, 2))
        self.status.pack(fill=tk.X, pady=(4, 0))

        root.bind('<Return>', lambda e: self.run_tool())
        self.cal_bar.load_default()

    # ----- Calibration file delegation -----
    def set_cal_data(self, data):
        self.actual_editor.set_data(data.get('actual', {}))
        self.gopro_editor.set_data(data.get('gopro', {}))
        self.update_preview()

    def get_cal_data(self):
        return self.cal_bar.get_data()

    def _on_delta_entry(self, event=None):
        text = self.delta_entry.get().strip()
        delta = _parse_delta(text)
        if delta is not None:
            self.file_table.manual_delta = delta
            # Sync gopro editor: gopro = actual - delta
            actual = self.actual_editor.get_data()
            cal_data = {'actual': actual, 'gopro': {}}
            ok, *rest = calibration.try_parse(cal_data)
            if ok:
                gopro_dt = rest[0] - delta
                self.gopro_editor.on_date_picked(gopro_dt, '')
            else:
                # Fall back to clearing delta display
                pass

    def update_preview(self):
        delta = _compute_delta(self.actual_editor, self.gopro_editor)
        if delta is not None:
            self.file_table.manual_delta = delta
            self.preview_var.set('')
            self.delta_entry.delete(0, tk.END)
            self.delta_entry.insert(0, _fmt_delta(delta))
        else:
            data = self.get_cal_data()
            ok, *rest = calibration.try_parse(data)
            err = rest[0] if rest else 'Invalid'
            self.preview_var.set(f'\u26a0 {err}')
            self.delta_entry.delete(0, tk.END)

    def _on_strategy_changed(self):
        delta = _compute_delta(self.actual_editor, self.gopro_editor)
        if delta is not None:
            self.file_table.manual_delta = delta

    # ----- Analyze -----
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

        self.set_status('Analyzing files...')
        self.root.update_idletasks()

        import analysis as an_mod
        try:
            result = an_mod.analyze(target)
            if result.total_files == 0:
                messagebox.showinfo('Analysis', 'No media files found in this directory.')
                self.set_status('Ready')
                return
            self.file_table.load_analysis(result)
            delta = _compute_delta(self.actual_editor, self.gopro_editor)
            if delta is not None:
                self.file_table.manual_delta = delta
            self.log(f'Analysis: {len(result.sets)} file sets, {result.total_files} files')
            for fs in result.sets:
                gps = 'GPS' if fs.has_any_gps else 'no GPS'
                self.log(f'  Set {fs.id}: {fs.kind} ({gps})')
            self.set_status(f'Ready — {len(result.sets)} sets, {result.total_files} files')
        except Exception as e:
            messagebox.showerror('Analysis Error', str(e))
            self.set_status('Error during analysis')

    # ----- GPS -----
    def auto_gps(self):
        target_dir = self.dir_var.get()
        if not target_dir:
            return

        target = Path(target_dir)
        if not target.is_dir():
            return

        import media
        files = media.collect(target)
        if not files:
            messagebox.showinfo("GPS", "No media files found in this directory.")
            return

        self.set_status('Searching for GPS data...')
        self.root.update_idletasks()

        gps_file = None
        gps_utc = None
        for f in files:
            gps_utc = media.read_gps_time(f)
            if gps_utc:
                gps_file = f
                break

        if not gps_file:
            messagebox.showinfo("GPS", "No files with GPS data found in this directory.")
            self.set_status('Ready')
            return

        tz_id = self.actual_editor.tz_var.get()
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None

        gps_utc_tz = gps_utc.replace(tzinfo=timezone.utc)
        if tz:
            actual_dt = gps_utc_tz.astimezone(tz)
        else:
            actual_dt = gps_utc_tz.astimezone()

        gopro_dt = media.read_embedded(gps_file, use_qt_utc=False)

        if not gopro_dt:
            messagebox.showerror("GPS", f"Could not read GoPro time from {gps_file.name}")
            self.set_status('Ready')
            return

        self.actual_editor.on_date_picked(actual_dt, tz_id)
        self.gopro_editor.on_date_picked(gopro_dt, '')

        self.log(f"Extracted calibration from GPS: {gps_file.name}")
        self.set_status('Ready')

    def auto_calibrate_from_gps(self):
        """Aggregate GPS deltas across all files using weighted median.

        Reads GPS time and embedded time for every file, filters out
        poor-quality GPS fixes via GPSHPositioningError, computes the
        weighted median delta, then sets the delta entry and switches
        to the Delta tab.  The calendar editors are also populated so
        the calibration file can be saved.
        """
        target_dir = self.dir_var.get()
        if not target_dir:
            return
        target = Path(target_dir)
        if not target.is_dir():
            return

        import media
        files = media.collect(target)
        if not files:
            messagebox.showinfo('Auto Calibrate', 'No media files found.')
            return

        self.set_status('Reading GPS data from all files...')
        self.root.update_idletasks()

        batch = media.read_tags_batch(files)
        accuracy = media.read_gps_accuracy_batch(files)

        pairs = []
        for f in files:
            embedded, gps = batch.get(f, (None, None))
            if embedded is None or gps is None:
                continue

            acc = accuracy.get(f, 99.99)
            if acc is None:
                acc = 99.99
            if acc >= 25.0 or acc == 99.99:
                continue

            delta = gps - embedded
            weight = 1.0 / (acc + 1.0)
            pairs.append((delta, weight, f, gps, embedded))

        if not pairs:
            self.log('No files with valid GPS fix found (need GPSHPositioningError < 25 m).')
            self.log('Falling back to single-file GPS extraction.')
            self.set_status('Ready')
            self.auto_gps()
            return

        from resolve import weighted_median_delta
        deltas = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        median = weighted_median_delta(deltas, weights)

        if median is None:
            self.log('Could not compute weighted median — falling back to single GPS.')
            self.set_status('Ready')
            self.auto_gps()
            return

        # Set the delta entry (primary output) and switch to the Delta tab
        self.cal_notebook.select(1)
        self.delta_entry.delete(0, tk.END)
        self.delta_entry.insert(0, _fmt_delta(median))

        # Also populate the calendar editors so the calibration file is consistent
        best = min(pairs, key=lambda c: abs((c[0] - median).total_seconds()))
        _, _, best_file, best_gps, best_emb = best
        tz_id = self.actual_editor.tz_var.get()
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None
        gps_utc_tz = best_gps.replace(tzinfo=timezone.utc)
        actual_dt = gps_utc_tz.astimezone(tz) if tz else gps_utc_tz.astimezone()
        self.actual_editor.on_date_picked(actual_dt, tz_id)
        self.gopro_editor.on_date_picked(best_emb, '')

        # Statistics
        mean_delta = sum(deltas, timedelta()) / len(deltas) if deltas else median
        self.log(f'Auto calibrate: {len(pairs)} files with valid GPS fix')
        self.log(f'  Deltas range: {min(deltas)} … {max(deltas)}')
        self.log(f'  Weighted median: {median}')
        self.log(f'  Mean: ~{mean_delta}')
        self.log(f'  Representative file: {best_file.name}')
        self.set_status('Ready')

    # ----- Run -----
    def run_tool(self):
        if self.running:
            return
        if self.file_table.analysis is None:
            if not messagebox.askyesno('No Analysis',
                                       'No file analysis was performed.\nRun correction anyway?'):
                return
        if self.file_table.analysis and not self.dry_run_var.get():
            dry_run_warn = 'No analysis data available.\n'
        else:
            dry_run_warn = ''

        self.running = True
        self._set_buttons(tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)

        self.log('Applying corrections...')
        self.set_status('Running...')

        def run():
            try:
                target_dir = Path(self.dir_var.get())
                dry_run = self.dry_run_var.get()

                if self.file_table.analysis is not None:
                    # Use cached plan — no recalculation
                    jobs = self.file_table.get_write_jobs()
                    if not jobs:
                        self.root.after(0, self.log, 'No files to process.')
                        self.root.after(0, self.on_finish, 0)
                        return

                    for job in jobs:
                        self.root.after(0, self.log, str(job.path.name))

                    if dry_run:
                        self.root.after(0, self.log, f'\nDRY RUN - {len(jobs)} would be processed')
                    else:
                        delta = _compute_delta(self.actual_editor, self.gopro_editor)
                        with Writer(target_dir, fix_btime=self.btime_var.get(),
                                    delta=delta, dry_run=False) as w:
                            summary = w.write_all(jobs)
                        self.root.after(0, self.log, f'\n{summary.written} corrected')
                else:
                    # No analysis — fallback to CLI subprocess (legacy path)
                    data = self.get_cal_data()
                    cmd = [sys.executable, str(SCRIPT_DIR / 'correct_timestamps.py'),
                           self.dir_var.get()]
                    if dry_run:
                        cmd.append('--dry-run')
                    btime = self.btime_var.get()
                    if btime != 'off':
                        cmd.append(f'--fix-btime={btime}')
                    cal_path = self.cal_bar.get_path()
                    if cal_path and Path(cal_path).exists():
                        cmd.extend(['--translation', cal_path])
                    else:
                        tmp = target_dir / '.gui_calibration.json'
                        calibration.save_json(tmp, data)
                        cmd.extend(['--translation', str(tmp)])
                    self.root.after(0, self.log, f'$ {" ".join(cmd)}\n')
                    self.process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1)
                    for line in self.process.stdout:
                        self.root.after(0, self.log, line.rstrip())
                    self.process.wait()
                    self.root.after(0, self.on_finish, self.process.returncode)
                    return

                self.root.after(0, self.on_finish, 0)
            except Exception as e:
                self.root.after(0, self.log, f'Error: {e}')
                self.root.after(0, self.on_finish, -1)

        threading.Thread(target=run, daemon=True).start()

    def _run_single_writer(self, label: str, write_fn):
        """Run a single write operation in a background thread."""
        if self.running:
            return
        jobs = self.file_table.get_write_jobs()
        if not jobs:
            self.log('No files to process.')
            return

        self.running = True
        self._set_buttons(tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)

        self.log(f'{label}...')
        self.set_status('Running...')

        def run():
            try:
                target_dir = Path(self.dir_var.get())
                delta = _compute_delta(self.actual_editor, self.gopro_editor)
                with Writer(target_dir, fix_btime='off', delta=delta, dry_run=False) as w:
                    for job in jobs:
                        self.root.after(0, self.log, str(job.path.name))
                        write_fn(w, job)
                self.root.after(0, self.log, f'\n{label} done — {len(jobs)} files')
                self.root.after(0, self.on_finish, 0)
            except Exception as e:
                self.root.after(0, self.log, f'Error: {e}')
                self.root.after(0, self.on_finish, -1)

        threading.Thread(target=run, daemon=True).start()

    def run_exif(self):
        self._run_single_writer('Exiftool', lambda w, j: w.write_embedded_only(j))

    def run_mtime(self):
        self._run_single_writer('mtime', lambda w, j: w.write_mtime_only(j))

    def run_btime(self):
        self._run_single_writer('btime', lambda w, j: w.write_btime_only(j))

    def validate_cal(self):
        data = self.get_cal_data()
        ok, *rest = calibration.try_parse(data)
        if ok:
            return True
        err = rest[0] if rest else 'Invalid values'
        messagebox.showerror('Calibration error',
                              f'Invalid calibration values:\n{err}')
        return False

    def _set_buttons(self, state):
        for btn in (self.run_btn, self.exif_btn, self.mtime_btn, self.btime_btn):
            btn.config(state=state)

    def on_finish(self, code):
        self.running = False
        self.process = None
        self._set_buttons(tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        if code == 0:
            self.set_status('Completed')
            self.log('\nDone.')
        else:
            self.set_status(f'Failed (exit {code})')
            self.log(f'\nFailed (exit {code})')
        self.cleanup_temp()

    def cleanup_temp(self):
        d = Path(self.dir_var.get())
        for name in ('.gui_calibration.json', '.gui_strategy.json'):
            p = d / name
            if p.exists():
                p.unlink()

    def cancel_run(self):
        if self.process:
            self.process.terminate()
            self.log('\nCancelled')
            self.set_status('Cancelled')
        self.on_finish(-1)

    # ----- Helpers -----
    def log(self, msg):
        self.output.config(state=tk.NORMAL)
        self.output.insert(tk.END, msg + '\n')
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def set_status(self, msg):
        self.status.config(text=msg)
        self.root.update_idletasks()

    def browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.dir_var.get())
        if d:
            self.dir_var.set(d)
            self.cal_bar.auto(d)


def main():
    root = tk.Tk()
    ToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
