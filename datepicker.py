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

        # Day-of-week headers
        for col, d in enumerate(('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')):
            ttk.Label(grid, text=d, width=4, anchor=tk.CENTER,
                      font=('', 8, 'bold')).grid(row=0, column=col, padx=1)

        # Empty cells before first day
        row = 1
        for col in range(first):
            ttk.Label(grid, width=4).grid(row=row, column=col, padx=1)

        # Day number buttons
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
