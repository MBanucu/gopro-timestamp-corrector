"""Timezone information panel — loads and displays IANA transition data."""

import os
import struct
import subprocess
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta, timezone
from pathlib import Path


_IANA_ID: str | None = None


# ── IANA ID detection ─────────────────────────────────────────


def _detect_iana_id():
    tz = os.environ.get('TZ', '').strip()
    if tz and '/' in tz:
        return tz
    try:
        r = subprocess.run(
            ['timedatectl', 'show', '--property=Timezone', '--value'],
            capture_output=True, text=True, timeout=2)
        if r.returncode == 0 and '/' in r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        p = Path('/etc/localtime').resolve()
        for parent in p.parents:
            if parent.name == 'zoneinfo':
                rel = str(p.relative_to(parent))
                if '/' in rel:
                    return rel
    except Exception:
        pass
    return None


def get_iana_id():
    global _IANA_ID
    if _IANA_ID is None:
        _IANA_ID = _detect_iana_id()
    return _IANA_ID


# ── TZif binary parser ────────────────────────────────────────


def _load_tzif(key):
    import zoneinfo

    for base in zoneinfo.TZPATH:
        path = os.path.join(base, key)
        if os.path.isfile(path):
            break
    else:
        return None

    with open(path, 'rb') as f:
        raw = f.read()

    if raw[:4] != b'TZif':
        return None

    isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = \
        struct.unpack('>6i', raw[20:44])

    pos = 44 + timecnt * 4 + timecnt * 1 + typecnt * 6 + charcnt
    pos += leapcnt * 8 + isstdcnt + isutcnt

    if raw[pos:pos + 4] != b'TZif':
        return None

    isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = \
        struct.unpack('>6i', raw[pos + 20:pos + 44])
    pos2 = pos + 44

    trans_times = struct.unpack(f'>{timecnt}q',
                                raw[pos2:pos2 + timecnt * 8])
    pos2 += timecnt * 8

    trans_idx = list(raw[pos2:pos2 + timecnt])
    pos2 += timecnt

    ttinfo = []
    for _ in range(typecnt):
        off32, dst, abbr_idx = struct.unpack('>ibb', raw[pos2:pos2 + 6])
        ttinfo.append((off32, bool(dst), abbr_idx))
        pos2 += 6

    abbr_block = raw[pos2:pos2 + charcnt]

    def _abbr(idx):
        end = abbr_block.index(0, idx) if 0 in abbr_block[idx:] else len(abbr_block)
        return abbr_block[idx:end].decode()

    return {
        'trans_times': trans_times,
        'trans_idx': trans_idx,
        'ttinfo': [(off, dst, _abbr(idx)) for off, dst, idx in ttinfo],
    }


# ── Data extraction ───────────────────────────────────────────


def _zone_all_transitions(iana_id):
    data = _load_tzif(iana_id)
    if data is None:
        return []

    result = []
    tt = data['ttinfo']
    trans = data['trans_times']
    idx = data['trans_idx']

    for i, (ts, ti) in enumerate(zip(trans, idx)):
        cur_off, cur_dst, cur_abbr = tt[ti]
        prev_abbr = tt[idx[i - 1]][2] if i > 0 else tt[idx[0]][2]
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        dt_local = dt_utc + timedelta(seconds=cur_off)
        result.append((dt_local.replace(tzinfo=None), prev_abbr, cur_abbr,
                       cur_off, cur_dst))
    return result


def _build_tz_info_text(iana_id):
    if not iana_id:
        return 'TZ: local'

    tt = _load_tzif(iana_id)
    if tt is None:
        return f'TZ: {iana_id}'

    lines = [f'TZ: {iana_id}']
    trans = _zone_all_transitions(iana_id)

    if not trans:
        off, dst, abbr = tt['ttinfo'][0]
        off_h = f'UTC{off // 3600:+03d}:{(abs(off) % 3600) // 60:02d}'
        lines.append(f'  Fixed offset  {off_h}  {abbr}  (no DST)')
        return '\n'.join(lines)

    by_year: dict[int, list] = {}
    for t in trans:
        y = t[0].year
        by_year.setdefault(y, []).append(t)

    for year in sorted(by_year):
        entries = by_year[year]
        year_parts = []
        for dt_local, before, after, off, dst in entries:
            off_s = f'UTC{off // 3600:+03d}:{(abs(off) % 3600) // 60:02d}'
            if before != after:
                year_parts.append(
                    f'{dt_local.strftime("%d %b %H:%M")}  '
                    f'{before}\u2192{after}  {off_s}')
            else:
                year_parts.append(
                    f'{dt_local.strftime("%d %b %H:%M")}  {off_s}  {after}')
        lines.append(f'  {year}:  {" | ".join(year_parts)}')

    return '\n'.join(lines)


# ── GUI panel ─────────────────────────────────────────────────


class TzInfoPanel(ttk.Frame):
    """Foldable panel showing the IANA timezone transition history."""

    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)

        iana = get_iana_id() or 'local'
        tz_text = _build_tz_info_text(iana)
        tz_lines = tz_text.split('\n', 1)
        heading = tz_lines[0]
        detail = tz_lines[1] if len(tz_lines) > 1 else ''

        style = ttk.Style(self)
        style.configure('TZ.TButton', font=('', 8), foreground='#888',
                        borderwidth=0, padding=(4, 0), anchor=tk.W,
                        relief=tk.FLAT)

        self._expanded = False
        self._toggle = ttk.Button(self, style='TZ.TButton',
                                  command=self._toggle)
        self._toggle.configure(text=f'\u25b6  {heading}  |  GPS is UTC')
        self._toggle.pack(fill=tk.X, padx=2, pady=(0, 0))

        self._detail_frame = ttk.Frame(self)
        self._detail_text = tk.Text(self._detail_frame, height=6, wrap=tk.WORD,
                                     font=('', 8), fg='#666', bg='#fafafa',
                                     relief=tk.FLAT, padx=6, pady=2)
        self._detail_text.insert('1.0', detail)
        self._detail_text.configure(state=tk.DISABLED)
        self._detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scroll = ttk.Scrollbar(self._detail_frame, orient=tk.VERTICAL,
                                command=self._detail_text.yview)
        self._detail_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _toggle(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._detail_frame.pack(fill=tk.X, padx=(16, 0))
            self._toggle.configure(
                text=self._toggle.cget('text').replace('\u25b6', '\u25bc'))
        else:
            self._detail_frame.pack_forget()
            self._toggle.configure(
                text=self._toggle.cget('text').replace('\u25bc', '\u25b6'))
