"""Calibration panel — unified Calendar editors + Delta entry + GPS extraction."""

import re
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta, timezone
from pathlib import Path

import calibration
from gui.editor import CalibrationEditor
from gui.file_table import _fmt_delta


def compute_delta(actual_editor, gopro_editor):
    actual = actual_editor.get_data()
    gopro = gopro_editor.get_data()
    data = {'actual': actual, 'gopro': gopro}
    ok, *rest = calibration.try_parse(data)
    if ok:
        actual_dt, gopro_dt = rest[0], rest[1]
        # If timezone info is present, convert to UTC first so both
        # sides are naive and can be subtracted.
        if actual_dt.tzinfo is not None:
            actual_dt = actual_dt.astimezone(timezone.utc).replace(tzinfo=None)
        if gopro_dt.tzinfo is not None:
            gopro_dt = gopro_dt.astimezone(timezone.utc).replace(tzinfo=None)
        return actual_dt - gopro_dt
    return None


def parse_delta(text: str) -> timedelta | None:
    """Parse a human-readable timedelta string.

    Accepted formats::

        +2h30m    -1d5h     2:30      -90m
        +2h30m15s    -1d5h30s500ms     0
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

    days = hours = minutes = seconds = ms = 0
    units = re.findall(r'(\d+(?:\.\d+)?)(ms|[dhms])', text)
    for val_s, unit in units:
        val = float(val_s)
        if unit == 'd':
            days = int(val)
        elif unit == 'h':
            hours = int(val)
        elif unit == 'm':
            minutes = int(val)
        elif unit == 's':
            seconds = int(val)
            ms = int(round((val - seconds) * 1000))
        elif unit == 'ms':
            ms = int(val)

    if not units:
        try:
            seconds = float(text)
            ms = int(round((seconds - int(seconds)) * 1000))
            seconds = int(seconds)
            minutes = 0
        except ValueError:
            return None

    d = timedelta(days=days, hours=hours, minutes=minutes,
                  seconds=seconds, milliseconds=ms)
    return -d if negative else d


class CalibrationPanel(ttk.Frame):
    """Calendar editors + Delta entry + GPS extraction — all visible at once.

    Parameters
    ----------
    parent : tk.Widget
    get_dir_fn : callable
        Returns the target directory path as a string.
    log_fn : callable
        Called with a message string to log.
    set_status_fn : callable
        Called with a status string.
    delta_changed_cb : callable
        Called when the manual delta changes.
    """

    def __init__(self, parent, get_dir_fn, log_fn, set_status_fn,
                 delta_changed_cb, **kw):
        super().__init__(parent, **kw)

        self._get_dir = get_dir_fn
        self._log = log_fn
        self._set_status = set_status_fn
        self._delta_cb = delta_changed_cb

        # ── Calendar editors (side by side) ────────────────────
        eds = ttk.Frame(self)
        eds.pack(fill=tk.X, pady=(0, 8))
        self.actual_editor = CalibrationEditor(eds, 'Actual local time')
        self.actual_editor.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.gopro_editor = CalibrationEditor(eds, 'GoPro local time')
        self.gopro_editor.grid(row=0, column=1, sticky='ew')
        eds.columnconfigure(0, weight=1, uniform='editor')
        eds.columnconfigure(1, weight=1, uniform='editor')

        # ── Delta spinboxes — days hours minutes seconds ms ────
        delta_row = ttk.Frame(self)
        delta_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(delta_row, text='\u0394 Offset:', width=10,
                  font=('', 9, 'bold')).pack(side=tk.LEFT)
        self.delta_sign_var = tk.StringVar(value='+')
        sign_btn = ttk.Button(delta_row, textvariable=self.delta_sign_var,
                              width=3, command=self._toggle_sign)
        sign_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.day_var = tk.StringVar(value='0')
        ttk.Spinbox(delta_row, textvariable=self.day_var, from_=0, to=9999,
                    width=4).pack(side=tk.LEFT)
        ttk.Label(delta_row, text='d', width=2).pack(side=tk.LEFT)
        self.hour_var = tk.StringVar(value='0')
        ttk.Spinbox(delta_row, textvariable=self.hour_var, from_=0, to=23,
                    width=2).pack(side=tk.LEFT)
        ttk.Label(delta_row, text='h', width=2).pack(side=tk.LEFT)
        self.min_var = tk.StringVar(value='0')
        ttk.Spinbox(delta_row, textvariable=self.min_var, from_=0, to=59,
                    width=2).pack(side=tk.LEFT)
        ttk.Label(delta_row, text='m', width=2).pack(side=tk.LEFT)
        self.sec_var = tk.StringVar(value='0')
        ttk.Spinbox(delta_row, textvariable=self.sec_var, from_=0, to=59,
                    width=2).pack(side=tk.LEFT)
        ttk.Label(delta_row, text='s', width=2).pack(side=tk.LEFT)
        self.ms_var = tk.StringVar(value='0')
        ttk.Spinbox(delta_row, textvariable=self.ms_var, from_=0, to=999,
                    width=3).pack(side=tk.LEFT)
        ttk.Label(delta_row, text='ms').pack(side=tk.LEFT)

        # ── GPS buttons ────────────────────────────────────────
        gps_row = ttk.Frame(self)
        gps_row.pack(fill=tk.X, pady=(4, 2))
        ttk.Button(gps_row, text='Single GPS...', command=self._auto_gps,
                   width=13).pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Button(gps_row, text='Auto calibrate',
                   command=self.auto_calibrate,
                   width=13).pack(side=tk.RIGHT)

        # ── Preview / status line ──────────────────────────────
        self._preview_var = tk.StringVar()
        status_line = ttk.Frame(self)
        status_line.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(status_line, textvariable=self._preview_var,
                  foreground='#c33').pack(side=tk.LEFT)

        # ── Traces ─────────────────────────────────────────────
        for ed in (self.actual_editor, self.gopro_editor):
            for var in (ed.date_var, ed.hour_var, ed.min_var,
                        ed.sec_var, ed.ms_var, ed.tz_var):
                var.trace_add('write', lambda *a: self._update_preview())
        for var in (self.day_var, self.hour_var, self.min_var,
                    self.sec_var, self.ms_var, self.delta_sign_var):
            var.trace_add('write', lambda *a: self._on_delta_spinbox())

    # ── Public API ─────────────────────────────────────────────

    def set_data(self, data: dict):
        """Set both editors from a calibration dict (keys ``actual`` / ``gopro``)."""
        self.actual_editor.set_data(data.get('actual', {}))
        self.gopro_editor.set_data(data.get('gopro', {}))
        self._update_preview()

    @property
    def manual_delta(self) -> timedelta | None:
        return compute_delta(self.actual_editor, self.gopro_editor)

    # ── Delta spinboxes ────────────────────────────────────────

    def _toggle_sign(self):
        self.delta_sign_var.set('-' if self.delta_sign_var.get() == '+' else '+')

    def _read_delta_from_spinboxes(self) -> timedelta | None:
        try:
            d = int(self.day_var.get() or '0')
            h = int(self.hour_var.get() or '0')
            m = int(self.min_var.get() or '0')
            s = int(self.sec_var.get() or '0')
            ms = int(self.ms_var.get() or '0')
        except ValueError:
            return None
        sign = -1 if self.delta_sign_var.get() == '-' else 1
        return sign * timedelta(days=d, hours=h, minutes=m,
                                seconds=s, milliseconds=ms)

    def _set_spinboxes_from_delta(self, delta: timedelta | None):
        if delta is None:
            self.day_var.set('0')
            self.hour_var.set('0')
            self.min_var.set('0')
            self.sec_var.set('0')
            self.ms_var.set('0')
            self.delta_sign_var.set('+')
            return
        negative = delta.total_seconds() < 0
        if negative:
            delta = -delta
        self.delta_sign_var.set('-' if negative else '+')
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        remainder = total_seconds % 86400
        hours = remainder // 3600
        remainder %= 3600
        minutes = remainder // 60
        seconds = remainder % 60
        ms = delta.microseconds // 1000
        self.day_var.set(str(days))
        self.hour_var.set(str(hours))
        self.min_var.set(str(minutes))
        self.sec_var.set(str(seconds))
        self.ms_var.set(str(ms))

    def _on_delta_spinbox(self):
        if getattr(self, '_updating_spinboxes', False):
            return
        delta = self._read_delta_from_spinboxes()
        if delta is not None:
            self._delta_cb(delta)
            actual = self.actual_editor.get_data()
            cal_data = {'actual': actual, 'gopro': {}}
            ok, *rest = calibration.try_parse(cal_data)
            if ok:
                self._updating_spinboxes = True
                self.gopro_editor.set_datetime(rest[0] - delta)
                self._updating_spinboxes = False

    def _update_preview(self):
        delta = compute_delta(self.actual_editor, self.gopro_editor)
        if delta is not None:
            self._delta_cb(delta)
            self._preview_var.set('')
            self._updating_spinboxes = True
            self._set_spinboxes_from_delta(delta)
            self._updating_spinboxes = False
        else:
            data = {'actual': self.actual_editor.get_data(),
                    'gopro': self.gopro_editor.get_data()}
            ok, *rest = calibration.try_parse(data)
            err = rest[0] if rest else 'Invalid'
            self._preview_var.set(f'\u26a0 {err}')
            self._set_spinboxes_from_delta(None)

    # ── GPS extraction ─────────────────────────────────────────

    def _auto_gps(self):
        target_dir = self._get_dir()
        if not target_dir:
            return
        target = Path(target_dir)
        if not target.is_dir():
            return

        import media
        files = media.collect(target)
        if not files:
            messagebox.showinfo('GPS', 'No media files found in this directory.')
            return

        self._set_status('Searching for GPS data...')

        gps_file = None
        gps_utc = None
        for f in files:
            gps_utc = media.read_gps_time(f)
            if gps_utc:
                gps_file = f
                break

        if not gps_file:
            messagebox.showinfo('GPS', 'No files with GPS data found.')
            self._set_status('Ready')
            return

        tz_id = self.actual_editor.tz_var.get()
        if not tz_id:
            from gui.tz_info import get_iana_id
            tz_id = get_iana_id() or ''
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None

        actual_dt = gps_utc.astimezone(tz) if tz else gps_utc.astimezone()
        gopro_utc = media.read_embedded(gps_file, use_qt_utc=False)

        if not gopro_utc:
            messagebox.showerror('GPS', f'Could not read GoPro time from {gps_file.name}')
            self._set_status('Ready')
            return

        gopro_dt = gopro_utc.astimezone(tz) if tz else gopro_utc.astimezone()
        self.actual_editor.set_datetime(actual_dt)
        self.gopro_editor.set_datetime(gopro_dt)
        self._log(f'Extracted calibration from GPS: {gps_file.name}')
        self._set_status('Ready')

    def auto_calibrate(self):
        target_dir = self._get_dir()
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

        self._set_status('Reading GPS data from all files...')

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
            # Both GPS and embedded are UTC (QuickTime per spec), so the
            # per-file delta directly gives the camera clock error.
            delta = gps - embedded
            weight = 1.0 / (acc + 1.0)
            pairs.append((delta, weight, f, gps, embedded))

        if not pairs:
            self._log('No files with valid GPS fix (need GPSHPositioningError < 25 m).')
            self._log('Falling back to single-file GPS extraction.')
            self._set_status('Ready')
            self._auto_gps()
            return

        from resolve import weighted_median_delta
        deltas = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        median = weighted_median_delta(deltas, weights)

        if median is None:
            self._log('Could not compute weighted median — falling back to single GPS.')
            self._set_status('Ready')
            self._auto_gps()
            return

        # Populate the calendar editors FIRST (traces will fire and try to
        # update the delta entry, which we overwrite right after).
        best = min(pairs, key=lambda c: abs((c[0] - median).total_seconds()))
        _, _, best_file, best_gps, best_emb = best

        # Detect local timezone for GPS→local conversion
        tz_id = self.actual_editor.tz_var.get()
        if not tz_id:
            from gui.tz_info import get_iana_id
            tz_id = get_iana_id() or ''
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None

        # Both shown in the same local timezone so they are directly comparable.
        actual_dt = best_gps.astimezone(tz) if tz else best_gps.astimezone()
        gopro_dt = best_emb.astimezone(tz) if tz else best_emb.astimezone()
        self.actual_editor.set_datetime(actual_dt)
        self.gopro_editor.set_datetime(gopro_dt)

        mean_delta = sum(deltas, timedelta()) / len(deltas) if deltas else median
        self._log(f'Auto calibrate: {len(pairs)} files with valid GPS fix')
        self._log(f'  Deltas range: {min(deltas)} … {max(deltas)}')
        self._log(f'  Weighted median: {median}')
        self._log(f'  Mean: ~{mean_delta}')
        self._log(f'  Representative file: {best_file.name}')
        self._set_status('Ready')
