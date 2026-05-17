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
import history
from writer import Writer, WriteJob
from gui.sidebar import Sidebar
from gui.steps.directory import StepDirectory
from gui.steps.calibration import StepCalibration
from gui.steps.review import StepReview
from gui.steps.run import StepRun


SCRIPT_DIR = Path(__file__).resolve().parent


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

        main = ttk.Frame(root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # --- Sidebar ---
        self.sidebar = Sidebar(
            main,
            on_step_click=self._on_step_click,
            on_history=self._open_history,
        )
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))

        # --- Content area ---
        self.content = ttk.Frame(main)
        self.content.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # --- Step panels ---
        self.step1 = StepDirectory(
            self.content,
            on_analyzed=self._on_analyzed,
            log_fn=self.log,
            set_status_fn=self.set_status,
        )
        self.step2 = StepCalibration(
            self.content,
            get_dir_fn=lambda: self.step1.dir_var.get(),
            log_fn=self.log,
            set_status_fn=self.set_status,
            delta_changed_cb=self._on_delta_changed,
        )
        self.step3 = StepReview(
            self.content,
            manual_delta_changed_cb=self._on_strategy_changed,
        )
        self.step4 = StepRun(
            self.content,
            log_fn=self.log,
            set_status_fn=self.set_status,
        )

        # --- Cross-step wiring ---
        self.step1.set_on_set_cal_data(lambda data: self.step2.cal_panel.set_data(data))
        self.step2.set_on_next(self._advance_2to3)
        self.step2.set_on_skip(self._skip_to_run)
        self.step3.set_on_back(lambda: self._show_step(2))
        self.step3.set_on_next(self._advance_3to4)
        self.step4.set_on_back(self._go_back_from_run)
        self.step4.set_commands(
            apply_all=self.run_tool,
            exif=self.run_exif,
            mtime=self.run_mtime,
            btime=self.run_btime,
            cancel=self.cancel_run,
        )

        # --- Navigation state ---
        self._current_step = 1
        self._step_completed = [False] * 5  # 1-indexed
        self._step_frames = [None, self.step1, self.step2, self.step3, self.step4]

        self._show_step(1)

        root.bind('<Return>', lambda e: self.run_tool())
        self.step1.cal_bar.load_default()

    # ===================== Step Navigation =====================

    def _show_step(self, n):
        for i in range(1, 5):
            self._step_frames[i].pack_forget()
        self._current_step = n
        self._step_frames[n].pack(fill=tk.BOTH, expand=True)
        self.sidebar.update_steps(self._current_step, self._step_completed)

    def _advance_2to3(self):
        self._step_completed[2] = True
        self._show_step(3)

    def _advance_3to4(self):
        self._step_completed[3] = True
        self._show_step(4)

    def _skip_to_run(self):
        self._step_completed[2] = True
        self._step_completed[3] = True
        self._show_step(4)

    def _go_back_from_run(self):
        self._show_step(3 if self._step_completed[3] else 2)

    def _on_step_click(self, n):
        if n == self._current_step:
            return
        if n < self._current_step:
            self._show_step(n)
            return
        if self._current_step == 2:
            if n == 4:
                self._skip_to_run()
            elif n == 3:
                self._advance_2to3()
            return
        if n == self._current_step + 1 and self._step_completed[self._current_step]:
            self._show_step(n)

    # ===================== Analysis callback =====================

    def _on_analyzed(self, result):
        self.step3.load_analysis(result)
        self._step_completed[1] = True
        self._show_step(2)

    # ===================== Calibration / Delta wiring =====================

    def _on_delta_changed(self, delta):
        self.step3.manual_delta = delta

    def _on_strategy_changed(self):
        delta = self.step2.manual_delta
        if delta is not None:
            self.step3.manual_delta = delta

    # ===================== History =====================

    def _open_history(self):
        target_dir = self.step1.dir_var.get()
        if not target_dir:
            messagebox.showinfo('History', 'Select a directory first.')
            return
        from gui.history_viewer import HistoryViewer
        HistoryViewer(self.root, Path(target_dir))

    # ===================== Log / Status (wired to step4) =====================

    def log(self, msg):
        self.step4.output.config(state=tk.NORMAL)
        self.step4.output.insert(tk.END, msg + '\n')
        self.step4.output.see(tk.END)
        self.step4.output.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def set_status(self, msg):
        self.step4.status.config(text=msg)
        self.root.update_idletasks()

    # ===================== Run logic =====================

    def get_cal_data(self):
        return self.step1.cal_bar.get_data()

    def run_tool(self):
        if self.running:
            return
        if self.step3.file_table.analysis is None:
            if not messagebox.askyesno('No Analysis',
                                       'No file analysis was performed.\nRun correction anyway?'):
                return

        self.running = True
        self.step4.set_buttons_enabled(False)
        self.step4.set_cancel_enabled(True)
        self.step4.clear_output()

        self.log('Applying corrections...')
        self.set_status('Running...')

        def run():
            try:
                target_dir = Path(self.step1.dir_var.get())
                dry_run = self.step4.dry_run_var.get()

                if self.step3.file_table.analysis is not None:
                    jobs = self.step3.get_write_jobs()
                    if not jobs:
                        self.root.after(0, self.log, 'No files to process.')
                        self.root.after(0, self.on_finish, 0)
                        return

                    for job in jobs:
                        self.root.after(0, self.log, str(job.path.name))

                    if dry_run:
                        self.root.after(0, self.log,
                                        f'\nDRY RUN - {len(jobs)} would be processed')
                    else:
                        delta = self.step2.manual_delta
                        decisions = self.step3.get_decisions()
                        history_meta = {
                            'fix_btime': self.step4.btime_var.get() or 'off',
                            'global_delta': str(delta) if delta else None,
                            'sets': {
                                sid: {'strategy': d['strategy']}
                                for sid, d in decisions.items()
                            },
                        }
                        run_dir = history.begin_run(target_dir, history_meta)
                        history.capture_before(run_dir, [j.path for j in jobs])

                        with Writer(target_dir,
                                    fix_btime=self.step4.btime_var.get(),
                                    delta=delta, dry_run=False) as w:
                            summary = w.write_all(jobs)

                        history.capture_after(run_dir, [j.path for j in jobs])
                        history.finalize_run(run_dir, summary.written,
                                             summary.skipped, summary.errors)
                        self.root.after(0, self.log,
                                        f'\n{summary.written} corrected')
                else:
                    data = self.get_cal_data()
                    cmd = [sys.executable,
                           str(SCRIPT_DIR / 'correct_timestamps.py'),
                           self.step1.dir_var.get()]
                    if dry_run:
                        cmd.append('--dry-run')
                    btime = self.step4.btime_var.get()
                    if btime != 'off':
                        cmd.append(f'--fix-btime={btime}')
                    cal_path = self.step1.cal_bar.get_path()
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
        if self.running:
            return
        jobs = self.step3.get_write_jobs()
        if not jobs:
            self.log('No files to process.')
            return

        self.running = True
        self.step4.set_buttons_enabled(False)
        self.step4.set_cancel_enabled(True)
        self.step4.clear_output()

        self.log(f'{label}...')
        self.set_status('Running...')

        def run():
            try:
                target_dir = Path(self.step1.dir_var.get())
                delta = self.step2.manual_delta

                history_meta = {
                    'partial_write': label,
                    'delta': str(delta) if delta else None,
                }
                run_dir = history.begin_run(target_dir, history_meta)
                history.capture_before(run_dir, [j.path for j in jobs])

                with Writer(target_dir, fix_btime='off', delta=delta,
                            dry_run=False) as w:
                    for job in jobs:
                        self.root.after(0, self.log, str(job.path.name))
                        write_fn(w, job)

                history.capture_after(run_dir, [j.path for j in jobs])
                history.finalize_run(run_dir, len(jobs))
                self.root.after(0, self.log,
                                f'\n{label} done \u2014 {len(jobs)} files')
                self.root.after(0, self.on_finish, 0)
            except Exception as e:
                self.root.after(0, self.log, f'Error: {e}')
                self.root.after(0, self.on_finish, -1)

        threading.Thread(target=run, daemon=True).start()

    def run_exif(self):
        self._run_single_writer('Exiftool',
                                lambda w, j: w.write_embedded_only(j))

    def run_mtime(self):
        self._run_single_writer('mtime',
                                lambda w, j: w.write_mtime_only(j))

    def run_btime(self):
        self._run_single_writer('btime',
                                lambda w, j: w.write_btime_only(j))

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
        self.step4.set_buttons_enabled(True)
        self.step4.set_cancel_enabled(False)
        if code == 0:
            self.set_status('Completed')
            self.log('\nDone.')
        else:
            self.set_status(f'Failed (exit {code})')
            self.log(f'\nFailed (exit {code})')
        self.cleanup_temp()

    def cleanup_temp(self):
        d = Path(self.step1.dir_var.get())
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


def main():
    root = tk.Tk()
    ToolGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
