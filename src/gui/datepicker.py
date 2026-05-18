import calendar
import subprocess
from datetime import date, datetime
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = ttk = None

from gui.time_selector import TimeSelector


class DatePicker(tk.Toplevel):
    def __init__(self, parent, callback):
        if tk is None:
            return
        super().__init__(parent)
        self.callback = callback
        self.title('Pick a date')
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        now = date.today()
        self.year = tk.IntVar(value=now.year)
        self.month = tk.IntVar(value=now.month)
        self.selected = None

        self.build()
        self.draw_days()
        self.geometry(f'+{parent.winfo_rootx()+100}+{parent.winfo_rooty()+100}')

    def build(self):
        nav = ttk.Frame(self)
        nav.pack(pady=4)
        ttk.Button(nav, text='‹', width=3, command=self.prev_month).pack(side=tk.LEFT, padx=2)
        self.month_label = ttk.Label(nav, text='', width=12, anchor=tk.CENTER, font=('', 10, 'bold'))
        self.month_label.pack(side=tk.LEFT, padx=4)
        self.year_spin = ttk.Spinbox(nav, from_=1970, to=2100, textvariable=self.year,
                                      width=5, command=self.draw_days)
        self.year_spin.pack(side=tk.LEFT)
        ttk.Button(nav, text='›', width=3, command=self.next_month).pack(side=tk.LEFT, padx=2)
        self.update_month_label()

        self.day_frame = ttk.Frame(self)
        self.day_frame.pack(padx=4, pady=(0, 4))

    def update_month_label(self):
        m = date(2000, self.month.get(), 1).strftime('%B')
        self.month_label.config(text=m)

    def prev_month(self):
        m = self.month.get() - 1
        if m < 1:
            m = 12
            self.year.set(self.year.get() - 1)
        self.month.set(m)
        self.update_month_label()
        self.draw_days()

    def next_month(self):
        m = self.month.get() + 1
        if m > 12:
            m = 1
            self.year.set(self.year.get() + 1)
        self.month.set(m)
        self.update_month_label()
        self.draw_days()

    def draw_days(self):
        for w in self.day_frame.winfo_children():
            w.destroy()

        y, m = self.year.get(), self.month.get()
        first = calendar.weekday(y, m, 1)
        days_in_month = calendar.monthrange(y, m)[1]

        grid = ttk.Frame(self.day_frame)
        grid.pack()

        for col, d in enumerate(('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')):
            ttk.Label(grid, text=d, width=4, anchor=tk.CENTER,
                      font=('', 8, 'bold')).grid(row=0, column=col, padx=1)

        row = 1
        for col in range(first):
            ttk.Label(grid, width=4).grid(row=row, column=col, padx=1)

        col = first
        for d in range(1, days_in_month + 1):
            btn = ttk.Button(grid, text=str(d), width=4,
                             command=lambda day=d: self.pick(day))
            btn.grid(row=row, column=col, padx=1)
            col += 1
            if col > 6:
                col = 0
                row += 1

    def pick(self, day):
        y, m = self.year.get(), self.month.get()
        self.callback(date(y, m, day))
        self.destroy()


class DateTimePicker(DatePicker):
    """A date picker with time and timezone fields."""

    def __init__(self, parent, callback, all_zones=None,
                 initial_hour=12, initial_minute=0, initial_second=0,
                 initial_ms=0, initial_tz=''):
        self._hour = initial_hour
        self._minute = initial_minute
        self._second = initial_second
        self._ms = initial_ms
        self._tz = initial_tz
        self._all_zones = all_zones or []
        super().__init__(parent, callback)

    def build(self):
        super().build()

        body = ttk.Frame(self)
        body.pack(fill=tk.X, padx=8, pady=(0, 8))

        # Time row
        self.time_selector = TimeSelector(body, label='Time:', label_width=6)
        self.time_selector.pack(fill=tk.X, pady=2)
        self.time_selector.set_time(hour=self._hour, minute=self._minute,
                                     second=self._second, ms=self._ms)
        self.hour_var = self.time_selector.hour_var
        self.min_var = self.time_selector.min_var
        self.sec_var = self.time_selector.sec_var
        self.ms_var = self.time_selector.ms_var

        # Timezone row
        row = ttk.Frame(body)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text='TZ:', width=6).pack(side=tk.LEFT)
        self.tz_var = tk.StringVar(value=self._tz)
        from gui.tzcombobox import FilteringCombobox
        self.tz_combo = FilteringCombobox(row, all_values=self._all_zones,
                                           textvariable=self.tz_var, width=30)
        self.tz_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Buttons
        btn_frame = ttk.Frame(body)
        btn_frame.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_frame, text='Now', width=8,
                   command=self._set_now).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_frame, text='Pick', width=8,
                   command=self._pick_day).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_frame, text='Cancel', width=8,
                   command=self.destroy).pack(side=tk.RIGHT)

        # Pick the clicked day from the calendar
        self._pending_day = None

    @staticmethod
    def _system_tz():
        try:
            r = subprocess.run(['timedatectl', 'show', '-p', 'Timezone', '--value'],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except Exception:
            pass
        try:
            p = Path('/etc/localtime')
            if p.exists():
                resolved = p.resolve()
                for i, part in enumerate(resolved.parts):
                    if part in ('zoneinfo', 'posix'):
                        return '/'.join(resolved.parts[i+1:])
        except Exception:
            pass
        return 'UTC'

    def _set_now(self):
        now = datetime.now()
        self.year.set(now.year)
        self.month.set(now.month)
        self.draw_days()
        self.time_selector.set_time(hour=now.hour, minute=now.minute,
                                     second=now.second, ms=now.microsecond // 1000)
        self.tz_var.set(self._system_tz())
        self._pending_day = now.day

    def _pick_day(self):
        if self._pending_day is not None:
            self.pick(self._pending_day)

    def pick(self, day):
        self._pending_day = day
        y, m = self.year.get(), self.month.get()
        tz = self.tz_var.get().strip()
        h, minute, s, ms = self.time_selector.get_time()
        dt = datetime(y, m, day, h, minute, s, ms * 1000)
        self.callback(dt, tz)
        self.destroy()

    def draw_days(self):
        old_commands = {}
        if hasattr(self, '_day_buttons'):
            old_commands.clear()
        super().draw_days()
        # Override day button commands to store the day and enable the Pick button
        grid_frame = None
        for w in self.day_frame.winfo_children():
            if isinstance(w, ttk.Frame):
                grid_frame = w
                break
        if not grid_frame:
            return
        for child in grid_frame.winfo_children():
            if isinstance(child, ttk.Button):
                try:
                    d = int(child.cget('text'))
                    child.configure(command=lambda day=d: self._select_day(day))
                except ValueError:
                    pass

    def _select_day(self, day):
        self._pending_day = day
