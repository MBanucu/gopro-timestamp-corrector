import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import AnalysisResult, FileInfo
from preview import (
    compute_preview, SetDecision, PreviewResult, FilePreview,
    STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP,
)


_COLUMNS = ('set', 'file', 'type', 'mtime', 'exif', 'gps', 'strategy', 'target')

_IANA_ID: str | None = None


def _detect_iana_id():
    import os, subprocess
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


def _get_iana_id():
    global _IANA_ID
    if _IANA_ID is None:
        _IANA_ID = _detect_iana_id()
    return _IANA_ID


def _load_tzif(key):
    """Parse the compiled TZif file for *key* and return transition data.

    Returns a dict with ``trans_times`` (tuple of int UTC timestamps),
    ``trans_idx`` (list of ttinfo indices), ``ttinfo`` (list of
    ``(offset_seconds, is_dst, abbreviation)`` tuples).
    """
    import os
    import struct
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

    # v1 header at offset 20
    isutcnt, isstdcnt, leapcnt, timecnt, typecnt, charcnt = \
        struct.unpack('>6i', raw[20:44])

    # end of v1 data
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


def _zone_all_transitions(iana_id):
    """Return ALL historical IANA transitions for *iana_id*.

    Returns a list of ``(local_datetime, abbr_before, abbr_after,
    offset_after_s, is_dst)`` tuples, sorted chronologically.
    Returns an empty list if the zone has no transitions or is not found.
    """
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
    """Build a multi-line string with the full IANA transition history."""
    if not iana_id:
        return 'TZ: local'

    tt = _load_tzif(iana_id)
    if tt is None:
        return f'TZ: {iana_id}'

    lines = [f'TZ: {iana_id}']
    trans = _zone_all_transitions(iana_id)

    if not trans:
        # Fixed-offset zone: show the single ttinfo
        off, dst, abbr = tt['ttinfo'][0]
        off_h = f'UTC{off//3600:+03d}:{(abs(off)%3600)//60:02d}'
        lines.append(f'  Fixed offset  {off_h}  {abbr}  (no DST)')
        return '\n'.join(lines)

    # Group by year
    by_year: dict[int, list] = {}
    for t in trans:
        y = t[0].year
        by_year.setdefault(y, []).append(t)

    for year in sorted(by_year):
        entries = by_year[year]
        year_parts = []
        for dt_local, before, after, off, dst in entries:
            off_s = f'UTC{off//3600:+03d}:{(abs(off)%3600)//60:02d}'
            if before != after:
                year_parts.append(
                    f'{dt_local.strftime("%d %b %H:%M")}  '
                    f'{before}\u2192{after}  {off_s}')
            else:
                year_parts.append(
                    f'{dt_local.strftime("%d %b %H:%M")}  {off_s}  {after}')
        lines.append(f'  {year}:  {" | ".join(year_parts)}')

    return '\n'.join(lines)


def _local_tz_info():
    now = datetime.now().astimezone()
    abbr = now.tzname() or ''
    offset = now.strftime('%z')
    offset = f'UTC{offset[:3]}:{offset[3:]}' if offset else 'UTC'
    iana = _get_iana_id()
    if iana:
        return f'{iana} ({abbr}, {offset})'
    return f'{abbr} ({offset})'


def _utc_to_local_with_tz(utc_dt):
    if utc_dt is None:
        return None, ''
    iana = _get_iana_id()
    if not iana:
        return utc_dt, ''
    import zoneinfo
    try:
        z = zoneinfo.ZoneInfo(iana)
        local_dt = utc_dt.replace(tzinfo=timezone.utc).astimezone(z)
        return local_dt.replace(tzinfo=None), local_dt.tzname() or ''
    except Exception:
        return utc_dt, ''





def _fmt(dt, tz_suffix=''):
    if dt is None:
        return '—'
    if isinstance(dt, datetime):
        base = dt.strftime('%Y-%m-%d %H:%M:%S')
        return f'{base} {tz_suffix}' if tz_suffix else base
    return str(dt)


def _fmt_delta(delta):
    if delta is None:
        return '—'
    negative = delta.total_seconds() < 0
    if negative:
        delta = -delta
    parts = []
    if delta.days:
        parts.append(f'{delta.days}d')
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    if hours:
        parts.append(f'{hours}h')
    if minutes or not parts:
        parts.append(f'{minutes}m')
    return ('-' if negative else '+') + ' '.join(parts)


class FileSetTable(ttk.Frame):
    def __init__(self, parent, manual_delta_changed_cb=None, **kw):
        super().__init__(parent, **kw)
        self.analysis: AnalysisResult | None = None
        self.decisions: dict[str, SetDecision] = {}
        self._manual_delta: timedelta = timedelta()
        self._delta_changed_cb = manual_delta_changed_cb

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)

        self.tree = ttk.Treeview(
            outer, columns=_COLUMNS,
            show='tree headings', selectmode='browse',
            height=8,
        )

        self.tree.heading('#0', text='', anchor=tk.W)
        self.tree.column('#0', width=0, stretch=False)

        col_widths = {
            'set': 70, 'file': 130, 'type': 50,
            'mtime': 180, 'exif': 180, 'gps': 180,
            'strategy': 80, 'target': 180,
        }
        headings = {
            'set': 'Set', 'file': 'File', 'type': 'Type',
            'mtime': 'FS mtime',
            'exif': 'EXIF time',
            'gps': 'GPS time (UTC)',
            'strategy': 'Strategy',
            'target': 'Target',
        }
        for col in _COLUMNS:
            self.tree.heading(col, text=headings[col], anchor=tk.W)
            self.tree.column(col, width=col_widths.get(col, 100), minwidth=50)

        vsb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.tag_configure('gps_avail', foreground='#2a7')
        self.tree.tag_configure('no_gps', foreground='#999')
        self.tree.tag_configure('set_row', font=('', 9, 'bold'))
        self.tree.tag_configure('changed', background='#efe')

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label='Use GPS time', command=lambda: self._set_strategy(STRATEGY_GPS))
        self.menu.add_command(label='Use Manual calibration', command=lambda: self._set_strategy(STRATEGY_MANUAL))
        self.menu.add_command(label='Skip', command=lambda: self._set_strategy(STRATEGY_SKIP))
        self.tree.bind('<Button-3>', self._show_menu)

        iana = _get_iana_id() or 'local'
        tz_text = _build_tz_info_text(iana)
        tz_lines = tz_text.split('\n', 1)
        heading = tz_lines[0]
        detail = tz_lines[1] if len(tz_lines) > 1 else ''

        style = ttk.Style(self)
        style.configure('TZ.TButton', font=('', 8), foreground='#888',
                        borderwidth=0, padding=(4, 0), anchor=tk.W,
                        relief=tk.FLAT)

        self._tz_expanded = False
        self._tz_toggle = ttk.Button(self, style='TZ.TButton',
                                     command=self._toggle_tz)
        self._tz_toggle.configure(text=f'\u25b6  {heading}  |  GPS is UTC')
        self._tz_toggle.pack(fill=tk.X, padx=2, pady=(0, 0))

        self._tz_detail_frame = ttk.Frame(self)
        self._tz_detail_text = tk.Text(self._tz_detail_frame, height=6, wrap=tk.WORD,
                                        font=('', 8), fg='#666', bg='#fafafa',
                                        relief=tk.FLAT, padx=6, pady=2)
        self._tz_detail_text.insert('1.0', detail)
        self._tz_detail_text.configure(state=tk.DISABLED)
        self._tz_detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tz_scroll = ttk.Scrollbar(self._tz_detail_frame, orient=tk.VERTICAL,
                                   command=self._tz_detail_text.yview)
        self._tz_detail_text.configure(yscrollcommand=tz_scroll.set)
        tz_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self._tz_detail_frame.pack(fill=tk.X, padx=(16, 0))
        self._tz_detail_frame.pack_forget()

        self._status_var = tk.StringVar(value='No files analyzed yet')
        status_bar = ttk.Label(self, textvariable=self._status_var,
                                foreground='gray', anchor=tk.W, padding=(4, 1))
        status_bar.pack(fill=tk.X)

    def _toggle_tz(self):
        self._tz_expanded = not self._tz_expanded
        if self._tz_expanded:
            self._tz_detail_frame.pack(fill=tk.X, padx=(16, 0))
            self._tz_toggle.configure(text=self._tz_toggle.cget('text').replace('\u25b6', '\u25bc'))
        else:
            self._tz_detail_frame.pack_forget()
            self._tz_toggle.configure(text=self._tz_toggle.cget('text').replace('\u25bc', '\u25b6'))

    # ---- Public API ----

    @property
    def manual_delta(self) -> timedelta:
        return self._manual_delta

    @manual_delta.setter
    def manual_delta(self, delta: timedelta):
        self._manual_delta = delta
        self._refresh_manual_previews()

    def load_analysis(self, analysis: AnalysisResult):
        self.analysis = analysis
        self.decisions = {}
        for fs in analysis.sets:
            if fs.has_any_gps:
                self.decisions[fs.id] = SetDecision(strategy=STRATEGY_GPS)
            else:
                self.decisions[fs.id] = SetDecision(
                    strategy=STRATEGY_MANUAL, manual_delta=self._manual_delta)
        self._rebuild_tree()
        self._status_var.set(
            f'{len(analysis.sets)} sets, {analysis.total_files} files')

    def get_decisions(self) -> dict[str, dict]:
        return {
            sid: {'strategy': d.strategy}
            for sid, d in self.decisions.items()
        }

    def get_write_jobs(self):
        """Return WriteJob list from current plan — no recalculation of targets."""
        if not self.analysis:
            return []
        import preview as pr_mod
        from writer import WriteJob
        plan = pr_mod.compute_preview(self.analysis, self.decisions, self._manual_delta)
        return [
            WriteJob(path=fp.path, target_embedded=fp.target_embedded, target_mtime=fp.target_mtime)
            for pr in plan for fp in pr.file_results
        ]

    def clear(self):
        self.analysis = None
        self.decisions = {}
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._status_var.set('No files analyzed yet')

    # ---- Internal ----

    def _rebuild_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.analysis:
            return

        previews = compute_preview(self.analysis, self.decisions, self._manual_delta)
        preview_map: dict[str, PreviewResult] = {p.set_id: p for p in previews}

        for fs in self.analysis.sets:
            dec = self.decisions.get(fs.id, SetDecision(strategy=STRATEGY_MANUAL))
            pr = preview_map.get(fs.id)

            gps_tag = 'gps_avail' if fs.has_any_gps else 'no_gps'
            set_iid = self.tree.insert('', tk.END,
                values=(
                    fs.id, '', fs.kind,
                    f'GPS: {"✓" if fs.has_any_gps else "—"}',
                    f'EMB: {"✓" if fs.has_any_embedded else "—"}',
                    '',
                    dec.strategy,
                    '',
                ),
                tags=('set_row', gps_tag),
                open=True,
            )

            for fi, fp in zip(fs.files, pr.file_results if pr else []):
                is_mp4_lrv = fi.ext in ('.mp4', '.lrv')
                fmt_mtime, mtime_tz = _utc_to_local_with_tz(fi.mtime)
                if is_mp4_lrv:
                    cur_emb, emb_tz = _utc_to_local_with_tz(fi.embedded_time)
                    tgt_emb, tgt_tz = _utc_to_local_with_tz(fp.target_embedded)
                else:
                    cur_emb, emb_tz = fi.embedded_time, ''
                    if fp.target_embedded is not None:
                        tgt_emb, tgt_tz = _utc_to_local_with_tz(fp.target_embedded)
                    elif fp.target_mtime is not None:
                        tgt_emb, tgt_tz = _utc_to_local_with_tz(fp.target_mtime)
                    else:
                        tgt_emb, tgt_tz = None, ''
                self.tree.insert(set_iid, tk.END,
                    values=(
                        '',
                        fi.path.name,
                        fi.ext.lstrip('.'),
                        _fmt(fmt_mtime, mtime_tz),
                        _fmt(cur_emb, emb_tz),
                        _fmt(fi.gps_time, 'UTC'),
                        dec.strategy,
                        _fmt(tgt_emb, tgt_tz),
                    ),
                    tags=(gps_tag,),
                )

    def _refresh_manual_previews(self):
        for sid, dec in self.decisions.items():
            if dec.strategy == STRATEGY_MANUAL:
                dec.manual_delta = self._manual_delta
        self._rebuild_tree()

    def _get_set_id_from_selection(self) -> str | None:
        sel = self.tree.selection()
        if not sel:
            return None
        item = sel[0]
        parent = self.tree.parent(item)
        iid = parent if parent else item
        vals = self.tree.item(iid, 'values')
        return vals[0] if vals else None

    def _show_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        set_id = self._get_set_id_from_selection()
        if set_id and set_id in self.decisions:
            self.menu.tk_popup(event.x_root, event.y_root)

    def set_strategy_for_set(self, set_id: str, strategy: str):
        if set_id not in self.decisions:
            return
        if strategy == STRATEGY_MANUAL:
            self.decisions[set_id] = SetDecision(
                strategy=strategy, manual_delta=self._manual_delta)
        else:
            self.decisions[set_id] = SetDecision(strategy=strategy)
        self._rebuild_tree()
        if self._delta_changed_cb:
            self._delta_changed_cb()

    def _set_strategy(self, strategy: str):
        set_id = self._get_set_id_from_selection()
        if set_id:
            self.set_strategy_for_set(set_id, strategy)
