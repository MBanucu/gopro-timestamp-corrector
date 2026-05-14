import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta, timezone

from analysis import AnalysisResult, FileInfo
from preview import (
    compute_preview, SetDecision, PreviewResult, FilePreview,
    STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP,
)


_COLUMNS = ('set', 'file', 'type', 'mtime', 'exif', 'gps', 'strategy', 'target')


def _fmt(dt):
    if dt is None:
        return '—'
    if isinstance(dt, datetime):
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    return str(dt)


def _local_tz_info():
    now = datetime.now().astimezone()
    return f'{now.tzname()} (UTC{now.strftime("%z")})'


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
            'mtime': 160, 'exif': 160, 'gps': 160,
            'strategy': 80, 'target': 160,
        }
        tz_label = _local_tz_info()
        headings = {
            'set': 'Set', 'file': 'File', 'type': 'Type',
            'mtime': f'FS mtime ({tz_label})',
            'exif': 'EXIF time (UTC)',
            'gps': 'GPS time (UTC)',
            'strategy': 'Strategy',
            'target': f'Target ({tz_label})',
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

        self._tz_var = tk.StringVar(value=f'System: {_local_tz_info()}  |  EXIF: exiftool QuickTimeUTC=1')
        tz_label = ttk.Label(self, textvariable=self._tz_var,
                              foreground='#888', anchor=tk.W, padding=(4, 0), font=('', 8))
        tz_label.pack(fill=tk.X)

        self._status_var = tk.StringVar(value='No files analyzed yet')
        status_bar = ttk.Label(self, textvariable=self._status_var,
                                foreground='gray', anchor=tk.W, padding=(4, 1))
        status_bar.pack(fill=tk.X)

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
                self.tree.insert(set_iid, tk.END,
                    values=(
                        '',
                        fi.path.name,
                        fi.ext.lstrip('.'),
                        _fmt(fi.mtime),
                        _fmt(fi.embedded_time),
                        _fmt(fi.gps_time),
                        dec.strategy,
                        _fmt(fp.target_embedded or fp.target_mtime),
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
