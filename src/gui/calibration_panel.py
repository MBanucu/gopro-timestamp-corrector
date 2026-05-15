"""Calibration panel — notebook with Calendar / Delta tabs and GPS extraction."""

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

        +2h30m    -1d5h     2:30      -90m      0
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

    days = hours = minutes = 0
    m = re.search(r'(\d+)d', text)
    if m:
        days = int(m.group(1))
    m = re.search(r'(\d+)h', text)
    if m:
        hours = int(m.group(1))
    m = re.search(r'(\d+)m', text)
    if m:
        minutes = int(m.group(1))

    if days == 0 and hours == 0 and minutes == 0:
        try:
            minutes = int(text)
        except ValueError:
            return None

    d = timedelta(days=days, hours=hours, minutes=minutes)
    return -d if negative else d


class CalibrationPanel(ttk.Frame):
    """Notebook with Calendar / Delta tabs and GPS extraction buttons.

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

        # ── Notebook ───────────────────────────────────────────
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.X, pady=4)

        # Tab 1: Calendar
        cal_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(cal_tab, text='Calendar')

        self.actual_editor = CalibrationEditor(cal_tab, 'Actual local time')
        self.actual_editor.grid(row=0, column=0, sticky='ew', padx=(0, 4))

        self.gopro_editor = CalibrationEditor(cal_tab, 'GoPro local time')
        self.gopro_editor.grid(row=0, column=1, sticky='ew')
        cal_tab.columnconfigure(0, weight=1, uniform='editor')
        cal_tab.columnconfigure(1, weight=1, uniform='editor')

        # Tab 2: Delta
        delta_tab = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(delta_tab, text='Delta')
        ttk.Label(delta_tab, text='Enter the time offset directly:',
                  font=('', 8)).pack(anchor=tk.W)
        delta_entry_frame = ttk.Frame(delta_tab)
        delta_entry_frame.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(delta_entry_frame, text='Delta:', foreground='#555').pack(
            side=tk.LEFT, padx=(0, 4))
        self.delta_entry = ttk.Entry(delta_entry_frame, width=24, font=('', 9))
        self.delta_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.delta_entry.bind('<KeyRelease>', self._on_delta_entry)
        self.delta_entry.bind('<FocusOut>', self._on_delta_entry)
        ttk.Label(delta_tab, text='Examples:  +2h30m   -1d5h   2:30   90m   0',
                  font=('', 7), foreground='#999').pack(anchor=tk.W)

        # ── GPS buttons ────────────────────────────────────────
        gps_row = ttk.Frame(self)
        gps_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(gps_row, text='Single GPS...', command=self._auto_gps,
                   width=13).pack(side=tk.RIGHT, padx=(2, 0))
        ttk.Button(gps_row, text='Auto calibrate',
                   command=self._auto_calibrate_from_gps,
                   width=13).pack(side=tk.RIGHT)

        # ── Preview / status line ──────────────────────────────
        self._preview_var = tk.StringVar()
        status_line = ttk.Frame(self)
        status_line.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(status_line, textvariable=self._preview_var,
                  foreground='#c33').pack(side=tk.LEFT)

        # ── Traces ─────────────────────────────────────────────
        for ed in (self.actual_editor, self.gopro_editor):
            for var in (ed.date_var, ed.hour_var, ed.min_var, ed.tz_var):
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
                tz_id = self.actual_editor.tz_var.get()
                self.gopro_editor.on_date_picked(rest[0] - delta, tz_id)

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

        gps_utc_tz = gps_utc.replace(tzinfo=timezone.utc)
        actual_dt = gps_utc_tz.astimezone(tz) if tz else gps_utc_tz.astimezone()
        gopro_dt = media.read_embedded(gps_file, use_qt_utc=False)

        if not gopro_dt:
            messagebox.showerror('GPS', f'Could not read GoPro time from {gps_file.name}')
            self._set_status('Ready')
            return

        self.actual_editor.on_date_picked(actual_dt, tz_id)
        self.gopro_editor.on_date_picked(gopro_dt, tz_id)
        self._log(f'Extracted calibration from GPS: {gps_file.name}')
        self._set_status('Ready')

    def _auto_calibrate_from_gps(self):
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
        tz_id = self.actual_editor.tz_var.get()
        if not tz_id:
            from gui.tz_info import get_iana_id
            tz_id = get_iana_id() or ''
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None
        gps_utc_tz = best_gps.replace(tzinfo=timezone.utc)
        actual_dt = gps_utc_tz.astimezone(tz) if tz else gps_utc_tz.astimezone()
        self.actual_editor.on_date_picked(actual_dt, tz_id)
        # The gopro editor gets the same timezone as the actual editor, since
        # both times are local times from the same recording location.
        self.gopro_editor.on_date_picked(best_emb, tz_id)

        # Re-apply the median delta after editor traces have fired, so the
        # delta callback and the delta entry both show the correct value.
        self._delta_cb(median)
        self.notebook.select(1)
        self.delta_entry.delete(0, tk.END)
        self.delta_entry.insert(0, _fmt_delta(median))

        mean_delta = sum(deltas, timedelta()) / len(deltas) if deltas else median
        self._log(f'Auto calibrate: {len(pairs)} files with valid GPS fix')
        self._log(f'  Deltas range: {min(deltas)} … {max(deltas)}')
        self._log(f'  Weighted median: {median}')
        self._log(f'  Mean: ~{mean_delta}')
        self._log(f'  Representative file: {best_file.name}')
        self._set_status('Ready')
