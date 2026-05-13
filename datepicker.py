import calendar
from datetime import date, datetime

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    tk = ttk = None


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
                 initial_hour=12, initial_minute=0, initial_tz=''):
        self._hour = initial_hour
        self._minute = initial_minute
        self._tz = initial_tz
        self._all_zones = all_zones or []
        super().__init__(parent, callback)

    def build(self):
        super().build()

        body = ttk.Frame(self)
        body.pack(fill=tk.X, padx=8, pady=(0, 8))

        # Time row
        row = ttk.Frame(body)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text='Time:', width=6).pack(side=tk.LEFT)
        self.hour_var = tk.IntVar(value=self._hour)
        self.min_var = tk.IntVar(value=self._minute)
        ttk.Spinbox(row, textvariable=self.hour_var, from_=0, to=23,
                    width=3, format='%02.0f').pack(side=tk.LEFT)
        ttk.Label(row, text=':').pack(side=tk.LEFT)
        ttk.Spinbox(row, textvariable=self.min_var, from_=0, to=59,
                    width=3, format='%02.0f').pack(side=tk.LEFT)

        # Timezone row
        row = ttk.Frame(body)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text='TZ:', width=6).pack(side=tk.LEFT)
        self.tz_var = tk.StringVar(value=self._tz)
        from tzcombobox import FilteringCombobox
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

    def _set_now(self):
        now = datetime.now()
        self.year.set(now.year)
        self.month.set(now.month)
        self.draw_days()
        self.hour_var.set(now.hour)
        self.min_var.set(now.minute)

    def _pick_day(self):
        if self._pending_day is not None:
            self.pick(self._pending_day)

    def pick(self, day):
        self._pending_day = day
        y, m = self.year.get(), self.month.get()
        tz = self.tz_var.get().strip()
        dt = datetime(y, m, day, self.hour_var.get(), self.min_var.get())
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
