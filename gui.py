#!/usr/bin/env python3
import subprocess
import sys
import threading
from pathlib import Path
from datetime import datetime, date
try:
    import zoneinfo
except ImportError:
    zoneinfo = None

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, scrolledtext, messagebox
except ImportError:
    print("tkinter not available")
    sys.exit(1)

import calibration
from datepicker import DatePicker
from tzcombobox import FilteringCombobox


SCRIPT_DIR = Path(__file__).resolve().parent


def get_all_tz_ids():
    if zoneinfo is None:
        return []
    return sorted(zoneinfo.available_timezones())


def resolve_tz_abbr(iana_id, dt):
    if not zoneinfo or not iana_id:
        return ''
    try:
        tz = zoneinfo.ZoneInfo(iana_id)
        return dt.replace(tzinfo=tz).tzname() or ''
    except Exception:
        return ''


class CalibrationEditor(ttk.LabelFrame):
    def __init__(self, parent, title, **kw):
        super().__init__(parent, text=title, padding=8, **kw)

        # Date row
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='Date:', width=8).pack(side=tk.LEFT)
        self.date_var = tk.StringVar()
        self.date_entry = ttk.Entry(row, textvariable=self.date_var, width=16)
        self.date_entry.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(row, text='📅', width=3, command=self.pick_date).pack(side=tk.LEFT)
        ttk.Label(row, text='  ISO: YYYY-MM-DD', foreground='gray').pack(side=tk.LEFT)

        # Time row
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='Time:', width=8).pack(side=tk.LEFT)
        self.hour_var = tk.StringVar()
        self.min_var = tk.StringVar()
        ttk.Spinbox(row, textvariable=self.hour_var, from_=0, to=23,
                    width=3, format='%02.0f').pack(side=tk.LEFT)
        ttk.Label(row, text=':').pack(side=tk.LEFT)
        ttk.Spinbox(row, textvariable=self.min_var, from_=0, to=59,
                    width=3, format='%02.0f').pack(side=tk.LEFT)
        ttk.Label(row, text='  HH:MM (24h)', foreground='gray').pack(side=tk.LEFT, padx=(4, 0))

        # Timezone row
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='TZ:', width=8).pack(side=tk.LEFT)
        self.tz_var = tk.StringVar()
        self.all_zones = get_all_tz_ids() if zoneinfo else []
        self.tz_combo = FilteringCombobox(row, all_values=self.all_zones,
                                           textvariable=self.tz_var, width=35)
        self.tz_combo.pack(side=tk.LEFT, padx=(0, 4))

        self.tz_abbr_var = tk.StringVar()
        self.tz_abbr_label = ttk.Label(row, textvariable=self.tz_abbr_var,
                                        foreground='gray', width=14)
        self.tz_abbr_label.pack(side=tk.LEFT)

        self.date_var.trace_add('write', lambda *a: self.update_abbr())
        self.tz_var.trace_add('write', lambda *a: self.update_abbr())
        self.hour_var.trace_add('write', lambda *a: self.update_abbr())
        self.min_var.trace_add('write', lambda *a: self.update_abbr())

        # DST warning
        self.dst_warn_var = tk.StringVar()
        self.dst_warn = ttk.Label(self, textvariable=self.dst_warn_var,
                                   foreground='#b33', wraplength=380, font=('', 9))
        self.dst_warn.pack(fill=tk.X, pady=(2, 0))

        # Fold selector (hidden by default)
        self.fold_var = tk.IntVar(value=0)
        self.fold_row = ttk.Frame(self)
        self.fold_rb1 = ttk.Radiobutton(
            self.fold_row, text='',
            variable=self.fold_var, value=0, command=self.on_fold_change)
        self.fold_rb2 = ttk.Radiobutton(
            self.fold_row, text='',
            variable=self.fold_var, value=1, command=self.on_fold_change)
        self.fold_rb1.pack(side=tk.LEFT, padx=(0, 8))
        self.fold_rb2.pack(side=tk.LEFT)

    def update_abbr(self):
        try:
            d_str = self.date_var.get().strip()
            tz_id = self.tz_var.get().strip()
            dt = datetime.strptime(d_str, '%Y-%m-%d') if d_str else None
            if dt and tz_id:
                abbr = resolve_tz_abbr(tz_id, dt)
                if abbr:
                    self.tz_abbr_var.set(f'({abbr})')
                else:
                    self.tz_abbr_var.set('')
            else:
                self.tz_abbr_var.set('')
        except Exception:
            self.tz_abbr_var.set('')
        self.update_dst()

    def update_dst(self):
        import dst as dst_mod
        try:
            d_str = self.date_var.get().strip()
            t_str = f"{int(self.hour_var.get()):02d}:{int(self.min_var.get()):02d}"
            tz_id = self.tz_var.get().strip()
            if d_str and tz_id:
                dt = datetime.strptime(f"{d_str} {t_str}", '%Y-%m-%d %H:%M')
                r = dst_mod.check(tz_id, dt)
                if r['ambiguous']:
                    self.dst_warn_var.set(r['message'])
                    if r['transition_type'] == 'fall_back':
                        self.fold_rb1.config(text=f"First ({r['abbr_before']})")
                        self.fold_rb2.config(text=f"Second ({r['abbr_after']})")
                        # On first detection: default to first occurrence
                        if not self.fold_row.winfo_ismapped():
                            self.fold_var.set(r['fold'])
                        self.fold_row.pack(fill=tk.X, pady=(2, 0))
                    else:
                        self.fold_row.pack_forget()
                else:
                    self.dst_warn_var.set('')
                    self.fold_row.pack_forget()
            else:
                self.dst_warn_var.set('')
                self.fold_row.pack_forget()
        except Exception:
            self.dst_warn_var.set('')
            self.fold_row.pack_forget()

    def on_fold_change(self):
        self.update_dst()

    def pick_date(self):
        DatePicker(self.master.master, self.on_date_picked)

    def on_date_picked(self, d):
        self.date_var.set(d.strftime('%Y-%m-%d'))

    def on_tz_change(self, *args):
        if getattr(self, '_tz_sel', False):
            self._tz_sel = False
            self.update_abbr()
            return
        typed = self.tz_var.get().strip()
        if not typed:
            self.tz_combo['values'] = self.all_zones
        else:
            lower = typed.lower()
            matches = [z for z in self.all_zones if lower in z.lower()]
            self.tz_combo['values'] = matches if matches else [typed]
        self.tz_combo.tk.eval('ttk::combobox::Post %s' % self.tz_combo)
        self.update_abbr()

    def set_data(self, side_data):
        d = side_data.get('date', '')
        t = side_data.get('time', '')
        # Try to parse ISO date from MM/DD/YY if needed
        if d and '/' in d and len(d) <= 10:
            for fmt in ('%m/%d/%y', '%m/%d/%Y'):
                try:
                    d = datetime.strptime(d, fmt).strftime('%Y-%m-%d')
                    break
                except ValueError:
                    pass
        self.date_var.set(d)
        if ':' in t:
            parts = t.split(':')
            self.hour_var.set(parts[0].zfill(2))
            self.min_var.set(parts[1].zfill(2))
        else:
            self.hour_var.set('00')
            self.min_var.set('00')
        tz = side_data.get('timezone', '')
        if tz:
            self.tz_var.set(tz)
        self.fold_var.set(side_data.get('fold', 0))
        self.update_abbr()

    def get_data(self):
        d = {}
        d['date'] = self.date_var.get().strip()
        h = self.hour_var.get().strip() or '00'
        m = self.min_var.get().strip() or '00'
        d['time'] = f"{int(h):02d}:{int(m):02d}"
        d['timezone'] = self.tz_var.get().strip()
        d['date_format'] = 'YYYY-MM-DD'
        d['time_format'] = 'HH:MM'
        d['fold'] = self.fold_var.get()
        return d


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
        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=4)
        ttk.Label(row, text='Calibration:', width=14).pack(side=tk.LEFT)
        self.cal_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.cal_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(row, text='Load...', command=self.load_cal, width=8).pack(side=tk.RIGHT, padx=(0, 2))
        ttk.Button(row, text='Save...', command=self.save_cal, width=8).pack(side=tk.RIGHT, padx=(0, 2))
        ttk.Button(row, text='Auto', command=self.auto_cal, width=6).pack(side=tk.RIGHT)

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
        self.load_default_cal()

    # ----- Calibration -----
    def load_default_cal(self):
        self.cal_data = calibration.default()
        self.actual_editor.set_data(self.cal_data['actual'])
        self.gopro_editor.set_data(self.cal_data['gopro'])
        self.cal_var.set('')
        self.update_preview()

    def get_cal_data(self):
        return {
            'actual': self.actual_editor.get_data(),
            'gopro': self.gopro_editor.get_data(),
        }

    def set_cal_data(self, data):
        self.cal_data = data
        self.actual_editor.set_data(data.get('actual', {}))
        self.gopro_editor.set_data(data.get('gopro', {}))
        self.update_preview()

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

    def load_cal(self):
        path = filedialog.askopenfilename(
            title='Load calibration',
            filetypes=[('JSON', '*.json'), ('Calibration', '*.txt'), ('All', '*')],
            initialdir=self.dir_var.get(),
        )
        if not path:
            return
        try:
            ext = Path(path).suffix.lower()
            if ext == '.json':
                data = calibration.load_json(path)
            else:
                data = calibration.from_text(path)
            self.set_cal_data(data)
            self.cal_var.set(path)
            self.log(f'Loaded: {path}')
        except Exception as e:
            messagebox.showerror('Load error', str(e))

    def save_cal(self):
        path = filedialog.asksaveasfilename(
            title='Save calibration',
            defaultextension='.json',
            filetypes=[('JSON', '*.json'), ('All', '*')],
            initialdir=self.dir_var.get(),
        )
        if not path:
            return
        try:
            data = self.get_cal_data()
            if Path(path).suffix.lower() == '.json':
                calibration.save_json(path, data)
            else:
                Path(path).write_text(calibration.to_text(data))
            self.cal_var.set(path)
            self.log(f'Saved: {path}')
        except Exception as e:
            messagebox.showerror('Save error', str(e))

    def auto_cal(self):
        p = Path(self.dir_var.get())
        for f in p.glob('*time translation*'):
            try:
                data = calibration.from_text(f)
                self.set_cal_data(data)
                self.cal_var.set(str(f))
                self.log(f'Auto-loaded: {f.name}')
                return
            except Exception:
                continue
        for f in p.glob('*.json'):
            try:
                data = calibration.load_json(f)
                self.set_cal_data(data)
                self.cal_var.set(str(f))
                self.log(f'Auto-loaded: {f.name}')
                return
            except Exception:
                continue
        self.log('No calibration file found')

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

        cal_path = self.cal_var.get().strip()
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
