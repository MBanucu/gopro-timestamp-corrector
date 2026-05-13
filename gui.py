#!/usr/bin/env python3
import subprocess
import sys
import threading
from pathlib import Path
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import calibration
from gui_editor import CalibrationEditor, get_all_tz_ids, resolve_tz_abbr
from gui_cal_file import CalibrationFileBar


SCRIPT_DIR = Path(__file__).resolve().parent


class ToolGUI:
    def __init__(self, root):
        self.root = root
        root.title('GoPro Timestamp Corrector')
        root.geometry('820x750')
        root.minsize(700, 580)

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
        ttk.Button(row, text='Browse...', command=self.browse_dir, width=10).pack(side=tk.RIGHT)

        # --- Calibration file ---
        self.cal_bar = CalibrationFileBar(main, on_set_data=self.set_cal_data,
                                           log_fn=self.log)
        self.cal_bar.pack(fill=tk.X, pady=4)


        # --- Calibration editors ---
        cal_frame = ttk.Frame(main)
        cal_frame.pack(fill=tk.X, pady=6)

        self.actual_editor = CalibrationEditor(cal_frame, 'Actual local time')
        self.actual_editor.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.gopro_editor = CalibrationEditor(cal_frame, 'GoPro local time')
        self.gopro_editor.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # --- Preview / delta ---
        self.preview_var = tk.StringVar()
        self.delta_var = tk.StringVar(value='Delta: —')
        status = ttk.Frame(main)
        status.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(status, textvariable=self.preview_var, foreground='#c33').pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.delta_var, foreground='#555').pack(side=tk.RIGHT)

        for ed in (self.actual_editor, self.gopro_editor):
            for var in (ed.date_var, ed.hour_var, ed.min_var, ed.tz_var):
                var.trace_add('write', lambda *a: self.update_preview())

        # --- Separator ---
        ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=4)

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
        self.reprocess_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags1, text='Reprocess (UTC fix)', variable=self.reprocess_var).pack(side=tk.LEFT, padx=(0, 16))
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(flags1, text='Force (ignore manifest)', variable=self.force_var).pack(side=tk.LEFT)

        # --- Run button ---
        btn_row = ttk.Frame(main)
        btn_row.pack(fill=tk.X, pady=4)
        self.run_btn = ttk.Button(btn_row, text='Run', command=self.run_tool, width=12)
        self.run_btn.pack(side=tk.LEFT)
        self.cancel_btn = ttk.Button(btn_row, text='Cancel', command=self.cancel_run, width=12, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))

        # --- Output ---
        out_frame = ttk.LabelFrame(main, text='Output', padding=4)
        out_frame.pack(fill=tk.BOTH, expand=True)

        self.output = scrolledtext.ScrolledText(out_frame, wrap=tk.WORD, font=('Consolas', 10),
                                                 bg='#1e1e1e', fg='#d4d4d4', insertbackground='white')
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

    def update_preview(self):
        data = self.get_cal_data()
        ok, *rest = calibration.try_parse(data)
        if ok:
            actual_dt, gopro_dt = rest
            delta = actual_dt - gopro_dt
            self.delta_var.set(
                f'Delta: {delta.days}d {delta.seconds // 3600}h '
                f'{(delta.seconds % 3600) // 60}m'
            )
            self.preview_var.set('')
        else:
            err = rest[0] if rest else 'Invalid'
            self.delta_var.set('Delta: —')
            self.preview_var.set(f'⚠ {err}')

    # ----- Run -----
    def run_tool(self):
        if self.running:
            return
        if not self.validate_cal():
            return

        self.running = True
        self.run_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)

        data = self.get_cal_data()
        cmd = [sys.executable, str(SCRIPT_DIR / 'correct_timestamps.py'), self.dir_var.get()]

        if self.dry_run_var.get():
            cmd.append('--dry-run')
        if self.reprocess_var.get():
            cmd.append('--reprocess')
        if self.force_var.get():
            cmd.append('--force')

        btime = self.btime_var.get()
        if btime != 'off':
            cmd.append(f'--fix-btime={btime}')

        cal_path = self.cal_bar.get_path()
        if cal_path and Path(cal_path).exists():
            cmd.extend(['--translation', cal_path])
        else:
            tmp = Path(self.dir_var.get()) / '.gui_calibration.json'
            calibration.save_json(tmp, data)
            cmd.extend(['--translation', str(tmp)])

        self.log(f'$ {" ".join(cmd)}\n')
        self.set_status('Running...')

        def run():
            try:
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for line in self.process.stdout:
                    self.root.after(0, self.log, line.rstrip())
                self.process.wait()
                self.root.after(0, self.on_finish, self.process.returncode)
            except Exception as e:
                self.root.after(0, self.log, f'Error: {e}')
                self.root.after(0, self.on_finish, -1)

        threading.Thread(target=run, daemon=True).start()

    def validate_cal(self):
        data = self.get_cal_data()
        ok, *rest = calibration.try_parse(data)
        if ok:
            return True
        err = rest[0] if rest else 'Invalid values'
        messagebox.showerror('Calibration error',
                             f'Invalid calibration values:\n{err}')
        return False

    def on_finish(self, code):
        self.running = False
        self.process = None
        self.run_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        if code == 0:
            self.set_status('Completed')
            self.log('\nDone.')
        else:
            self.set_status(f'Failed (exit {code})')
            self.log(f'\nFailed (exit {code})')
        self.cleanup_temp()

    def cleanup_temp(self):
        tmp = Path(self.dir_var.get()) / '.gui_calibration.json'
        if tmp.exists():
            tmp.unlink()

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
            self.auto_cal()


def main():
    root = tk.Tk()
    ToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
