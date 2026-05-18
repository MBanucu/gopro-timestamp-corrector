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
import btime
from options import BTIME_OFF
from plan import Planner, PlanBuilder, CorrectionPlan
from writer import Writer, WriteJob
from gui.sidebar import Sidebar
from gui.steps.directory import StepDirectory
from gui.steps.review import StepReview
from gui.steps.plan import StepPlan
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

        # --- Shared output log + status bar (visible on every step) ---
        self._build_bottom_bar()

        # --- Step panels ---
        self.step1 = StepDirectory(
            self.content,
            on_analyzed=self._on_analyzed,
            log_fn=self.log,
            set_status_fn=self.set_status,
        )
        self.step2 = StepReview(
            self.content,
            get_dir_fn=lambda: self.step1.dir_var.get(),
            log_fn=self.log,
            set_status_fn=self.set_status,
            delta_changed_cb=self._on_delta_changed,
        )
        self.step3 = StepPlan(self.content)
        self.step4 = StepRun(self.content)

        # --- Cross-step wiring ---
        self.step1.set_on_set_cal_data(lambda data: self.step2.cal_panel.set_data(data))
        self.step2.set_on_back(lambda: self._show_step(1))
        self.step2.set_on_next(self._advance_to_plan)
        self.step3.set_on_back(self._go_back_from_plan)
        self.step3.set_on_next(self._advance_to_run)
        self.step4.set_on_back(self._go_back_from_run)
        self.step4.set_commands(
            apply=self.run_tool,
            cancel=self.cancel_run,
        )

        # --- Navigation state ---
        self._current_step = 1
        self._step_completed = [False] * 5  # 1-indexed: indices 1-4
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

    def _advance_to_plan(self):
        self._step_completed[2] = True
        target_dir = Path(self.step1.dir_var.get())
        if target_dir.is_dir():
            fs_type = btime.detect_fs(target_dir)
            self.step3.set_filesystem(fs_type)
        self._show_step(3)

    def _advance_to_run(self):
        self._step_completed[3] = True
        plan = self.step2.plan
        if plan is not None:
            target_dir = Path(self.step1.dir_var.get())
            instructions = PlanBuilder().build(
                self.step3.planner, plan, target_dir)
            self.step4.set_instructions(instructions)
        else:
            self.step4.set_summary('No analysis loaded \u2014 will run CLI fallback.')
        self._show_step(4)

    def _go_back_from_plan(self):
        self._show_step(2)

    def _go_back_from_run(self):
        self._show_step(3)

    def _on_step_click(self, n):
        if n == self._current_step:
            return
        if n < self._current_step:
            self._show_step(n)
            return
        if n == self._current_step + 1 and self._step_completed[self._current_step]:
            self._show_step(n)

    # ===================== Analysis callback =====================

    def _on_analyzed(self, result):
        self.step2.load_analysis(result)
        self._step_completed[1] = True
        self._show_step(2)
        self.step2.auto_calibrate()

    # ===================== Calibration / Delta wiring =====================

    def _on_delta_changed(self, delta):
        self.step2.manual_delta = delta

    # ===================== History =====================

    def _open_history(self):
        target_dir = self.step1.dir_var.get()
        if not target_dir:
            messagebox.showinfo('History', 'Select a directory first.')
            return
        from gui.history_viewer import HistoryViewer
        HistoryViewer(self.root, Path(target_dir))

    # ===================== Shared output log + status bar =====================

    def _build_bottom_bar(self):
        bottom = ttk.Frame(self.content)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)

        out_frame = ttk.LabelFrame(bottom, text='Output', padding=4)
        out_frame.pack(fill=tk.BOTH, expand=False, pady=(4, 0))

        self.output = scrolledtext.ScrolledText(
            out_frame, wrap=tk.WORD, font=('Consolas', 10),
            bg='#1e1e1e', fg='#d4d4d4', insertbackground='white', height=6)
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.config(state=tk.DISABLED)

        self.status = ttk.Label(bottom, text='Ready', relief=tk.SUNKEN,
                                anchor=tk.W, padding=(4, 2))
        self.status.pack(fill=tk.X, pady=(4, 0))

    def log(self, msg):
        self.output.config(state=tk.NORMAL)
        self.output.insert(tk.END, msg + '\n')
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)
        self.root.update_idletasks()

    def set_status(self, msg):
        self.status.config(text=msg)
        self.root.update_idletasks()

    def clear_output(self):
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)

    # ===================== Run logic =====================

    def get_cal_data(self):
        return self.step1.cal_bar.get_data()

    def run_tool(self):
        if self.running:
            return
        if self.step2.file_table.plan is None:
            if not messagebox.askyesno('No Analysis',
                                       'No file analysis was performed.\nRun correction anyway?'):
                return

        self.running = True
        self.step4.set_buttons_enabled(False)
        self.step4.set_cancel_enabled(True)
        self.step4.clear_output()

        instruction_items = list(self.step4._instruction_items)
        log_fn = lambda msg: self.root.after(0, self.step4.log, msg)
        progress_fn = lambda idx, status: self.root.after(
            0, self.step4.update_instruction, idx, status)

        def run():
            try:
                from plan import PlanBuilder
                instructions = self.step4._instruction_items
                # Re‑build instructions from the planner (they may have
                # changed since _advance_to_run was called).
                plan = self.step2.plan
                target_dir = Path(self.step1.dir_var.get())
                if plan is not None:
                    instructions = PlanBuilder().build(
                        self.step3.planner, plan, target_dir)
                    # Push updated instructions to the UI
                    self.root.after(0, self.step4.set_instructions, instructions)

                result = PlanBuilder().execute(
                    instructions, log_fn=log_fn, progress_fn=progress_fn)
                code = result.get('exit_code', 0)
                self.root.after(0, self.on_finish, code)
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
