"""Calibration editor widget for the GUI."""

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone

try:
    import zoneinfo
except ImportError:
    zoneinfo = None

import dst as dst_mod

from gui.datepicker import DateTimePicker
from gui.tzcombobox import FilteringCombobox


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
        self.date_entry = ttk.Entry(row, textvariable=self.date_var, width=14)
        self.date_entry.pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(row, text='📅', width=3, command=self.pick_date).pack(side=tk.LEFT)
        self.date_fmt_label = ttk.Label(row, text='  ISO: YYYY-MM-DD',
                                         foreground='gray')
        self.date_fmt_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

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
        ttk.Label(row, text=':').pack(side=tk.LEFT)
        self.sec_var = tk.StringVar()
        ttk.Spinbox(row, textvariable=self.sec_var, from_=0, to=59,
                    width=3, format='%02.0f').pack(side=tk.LEFT)
        ttk.Label(row, text='.').pack(side=tk.LEFT)
        self.ms_var = tk.StringVar()
        ttk.Spinbox(row, textvariable=self.ms_var, from_=0, to=999,
                    width=4, format='%03.0f').pack(side=tk.LEFT)
        ttk.Label(row, text='  HH:MM:SS.mmm (24h)', foreground='gray').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        # Timezone row
        row = ttk.Frame(self)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text='TZ:', width=8).pack(side=tk.LEFT)
        self.tz_var = tk.StringVar()
        self.all_zones = get_all_tz_ids() if zoneinfo else []
        self.tz_combo = FilteringCombobox(row, all_values=self.all_zones,
                                           textvariable=self.tz_var)
        self.tz_combo.pack(side=tk.LEFT, padx=(0, 4))
        self._tz_blink_id = None
        self._date_blink_id = None

        self.tz_abbr_var = tk.StringVar()
        self.tz_abbr_label = ttk.Label(row, textvariable=self.tz_abbr_var,
                                        width=14)
        self.tz_abbr_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.date_var.trace_add('write', lambda *a: self.update_abbr())
        self.date_var.trace_add('write', lambda *a: self._update_date_label())
        self.tz_var.trace_add('write', lambda *a: self.update_abbr())
        self.tz_var.trace_add('write', lambda *a: self._update_tz_utc_label())
        self.hour_var.trace_add('write', lambda *a: self.update_abbr())
        self.min_var.trace_add('write', lambda *a: self.update_abbr())

        # DST warning
        self.dst_warn_var = tk.StringVar()
        self.dst_warn = ttk.Label(self, textvariable=self.dst_warn_var,
                                   foreground='#b33', wraplength=380, font=('', 9))
        self.dst_warn.pack(fill=tk.X, pady=(2, 0))

        self._update_tz_utc_label()
        self._update_date_label()

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
            if dt and tz_id and self._tz_is_valid(tz_id):
                abbr = resolve_tz_abbr(tz_id, dt)
                if abbr:
                    self.tz_abbr_var.set(f'({abbr})')
                else:
                    self.tz_abbr_var.set('')
            elif not tz_id or not self._tz_is_valid(tz_id):
                pass  # _update_tz_utc_label handles the (UTC) warning
            else:
                self.tz_abbr_var.set('')
        except Exception:
            self.tz_abbr_var.set('')
        self.update_dst()

    def update_dst(self):
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
        try:
            h = int(self.hour_var.get())
        except ValueError:
            h = 12
        try:
            m = int(self.min_var.get())
        except ValueError:
            m = 0
        tz = self.tz_var.get()
        DateTimePicker(self.master.master, self.on_date_picked,
                       all_zones=self.all_zones,
                       initial_hour=h, initial_minute=m, initial_tz=tz)

    def _tz_is_valid(self, tz_id):
        if not tz_id:
            return False
        if not zoneinfo:
            return True
        try:
            zoneinfo.ZoneInfo(tz_id)
            return True
        except (Exception, ImportError):
            return False

    def _update_tz_utc_label(self):
        """Show (UTC) blinking red when TZ is empty or invalid, else stop blink."""
        if self._tz_blink_id:
            self.after_cancel(self._tz_blink_id)
            self._tz_blink_id = None
        tz_id = self.tz_var.get().strip()
        if self._tz_is_valid(tz_id):
            self.tz_abbr_label.configure(foreground='gray')
        else:
            self.tz_abbr_var.set('(UTC)')
            self._tz_blink(True)

    def _tz_blink(self, visible):
        self.tz_abbr_label.configure(
            foreground='red' if visible else '#888')
        self._tz_blink_id = self.after(600, self._tz_blink, not visible)

    def _date_blink(self, visible):
        self.date_fmt_label.configure(
            foreground='red' if visible else '#888')
        self._date_blink_id = self.after(600, self._date_blink, not visible)

    def _update_date_label(self):
        """Blink the date format label red when the date field is empty."""
        if self._date_blink_id:
            self.after_cancel(self._date_blink_id)
            self._date_blink_id = None
        if self.date_var.get().strip():
            self.date_fmt_label.configure(foreground='gray')
        else:
            self._date_blink(True)

    def _tzinfo_to_id(self, tzinfo) -> str:
        """Convert a ``tzinfo`` object to an IANA timezone ID string."""
        if tzinfo is None:
            return ''
        if zoneinfo and isinstance(tzinfo, zoneinfo.ZoneInfo):
            return tzinfo.key
        if isinstance(tzinfo, timezone):
            return 'UTC'
        return ''

    def set_datetime(self, dt):
        """Set date, time and timezone from a single *dt* (aware or naive).

        If *dt* is timezone-aware the editor's timezone is set from its
        ``tzinfo`` attribute (supports ``ZoneInfo`` and ``timezone.utc``).
        If *dt* is naive the timezone field is cleared.
        """
        self.date_var.set(dt.strftime('%Y-%m-%d'))
        self.hour_var.set(str(dt.hour).zfill(2))
        self.min_var.set(str(dt.minute).zfill(2))
        self.sec_var.set(str(dt.second).zfill(2))
        self.ms_var.set(str(dt.microsecond // 1000).zfill(3))
        tz_id = self._tzinfo_to_id(dt.tzinfo)
        if tz_id:
            self.tz_var.set(tz_id)

    def on_date_picked(self, dt, tz):
        self.date_var.set(dt.strftime('%Y-%m-%d'))
        self.hour_var.set(str(dt.hour).zfill(2))
        self.min_var.set(str(dt.minute).zfill(2))
        self.sec_var.set(str(dt.second).zfill(2))
        self.ms_var.set(str(dt.microsecond // 1000).zfill(3))
        if tz:
            self.tz_var.set(tz)

    def set_data(self, side_data):
        d = side_data.get('date', '')
        t = side_data.get('time', '')
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
            sec_ms = (parts[2] if len(parts) > 2 else '00').split('.')
            self.sec_var.set(sec_ms[0].zfill(2))
            self.ms_var.set(sec_ms[1].zfill(3) if len(sec_ms) > 1 else '000')
        else:
            self.hour_var.set('00')
            self.min_var.set('00')
            self.sec_var.set('00')
            self.ms_var.set('000')
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
        s = self.sec_var.get().strip() or '00'
        ms = self.ms_var.get().strip() or '0'
        d['time'] = f"{int(h):02d}:{int(m):02d}:{int(s):02d}.{int(ms):03d}"
        d['timezone'] = self.tz_var.get().strip()
        d['date_format'] = 'YYYY-MM-DD'
        d['time_format'] = 'HH:MM:SS.mmm'
        d['fold'] = self.fold_var.get()
        return d
