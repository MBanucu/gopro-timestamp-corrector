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

        # ── Delta entry ────────────────────────────────────────
        delta_row = ttk.Frame(self)
        delta_row.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(delta_row, text='\u0394 Offset:', width=10,
                  font=('', 9, 'bold')).pack(side=tk.LEFT)
        self.delta_entry = ttk.Entry(delta_row, width=24, font=('', 9))
        self.delta_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.delta_entry.bind('<KeyRelease>', self._on_delta_entry)
        self.delta_entry.bind('<FocusOut>', self._on_delta_entry)
        ttk.Label(delta_row,
                  text='Examples:  +2h30m   -1d5h   2:30   90m   0',
                  font=('', 7), foreground='#999').pack(side=tk.LEFT)

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

    # ── Public API ─────────────────────────────────────────────

    def set_data(self, data: dict):
        """Set both editors from a calibration dict (keys ``actual`` / ``gopro``)."""
        self.actual_editor.set_data(data.get('actual', {}))
        self.gopro_editor.set_data(data.get('gopro', {}))
        self._update_preview()

    @property
    def manual_delta(self) -> timedelta | None:
        return compute_delta(self.actual_editor, self.gopro_editor)

    # ── Delta entry ────────────────────────────────────────────

    def _on_delta_entry(self, event=None):
        text = self.delta_entry.get().strip()
        delta = parse_delta(text)
        if delta is not None:
            self._delta_cb(delta)
            actual = self.actual_editor.get_data()
            cal_data = {'actual': actual, 'gopro': {}}
            ok, *rest = calibration.try_parse(cal_data)
            if ok:
                self.gopro_editor.set_datetime(rest[0] - delta)

    def _update_preview(self):
        delta = compute_delta(self.actual_editor, self.gopro_editor)
        if delta is not None:
            self._delta_cb(delta)
            self._preview_var.set('')
            self.delta_entry.delete(0, tk.END)
            self.delta_entry.insert(0, _fmt_delta(delta))
        else:
            data = {'actual': self.actual_editor.get_data(),
                    'gopro': self.gopro_editor.get_data()}
            ok, *rest = calibration.try_parse(data)
            err = rest[0] if rest else 'Invalid'
            self._preview_var.set(f'\u26a0 {err}')
            self.delta_entry.delete(0, tk.END)

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
