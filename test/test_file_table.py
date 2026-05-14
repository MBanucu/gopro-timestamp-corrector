"""Tests for the FileSetTable GUI widget."""

import unittest
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
from pathlib import Path

from analysis import AnalysisResult, FileSet, FileInfo
from gui_file_table import FileSetTable
from preview import SetDecision, STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP


def _make_fs(set_id, exts, has_gps=True, emb_time=None, mtime=None):
    now = datetime(2026, 5, 14, 18, 0, 0)
    emb = emb_time or datetime(2026, 5, 14, 16, 0, 0)
    gps = datetime(2026, 5, 14, 12, 0, 0) if has_gps else None
    base_mtime = mtime or now
    files = []
    for ext in exts:
        prefix = 'GL' if ext == '.lrv' else 'GX'
        stem = f'{prefix}0{set_id}'
        files.append(FileInfo(
            path=Path(f'/d/{stem}{ext}'),
            stem=stem,
            ext=ext,
            mtime=base_mtime,
            embedded_time=emb if ext != '.thm' else None,
            gps_time=gps if ext in ('.mp4', '.lrv') else None,
        ))
    return FileSet(id=set_id, files=files)


class TestFileSetTable(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()

        self.table = FileSetTable(self.root)
        self.table.pack()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def _tree_rows(self):
        """Return list of (iid, parent, values) for all rows."""
        rows = []
        for item in self.table.tree.get_children():
            rows.append((item, '',
                         self.table.tree.item(item, 'values')))
            for child in self.table.tree.get_children(item):
                rows.append((child, item,
                             self.table.tree.item(child, 'values')))
        return rows

    def test_empty_initially(self):
        rows = self._tree_rows()
        self.assertEqual(len(rows), 0)
        self.assertEqual(self.table._status_var.get(), 'No files analyzed yet')

    def test_load_analysis_creates_rows(self):
        fs = _make_fs('010001', ['.mp4', '.lrv', '.thm'])
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        self.assertEqual(len(rows), 4)  # 1 parent + 3 children
        parent = rows[0]
        self.assertEqual(parent[2][0], '010001')  # set ID in first column
        self.assertEqual(parent[2][6], 'gps')     # strategy = GPS (has GPS)

    def test_load_analysis_no_gps_defaults_to_manual(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=False)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        parent = rows[0]
        self.assertEqual(parent[2][6], 'manual')

    def test_multiple_sets(self):
        sets = [
            _make_fs('010001', ['.mp4', '.lrv', '.thm']),
            _make_fs('010002', ['.mp4', '.lrv']),
            _make_fs('010003', ['.mp4']),
        ]
        ar = AnalysisResult(directory='/d', sets=sets)
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        # 3 parents + (3+2+1) children = 9 rows
        self.assertEqual(len(rows), 9)
        self.assertIn('3 sets, 6 files', self.table._status_var.get())

    def test_clear_removes_all(self):
        fs = _make_fs('010001', ['.mp4'])
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)
        self.assertEqual(len(self._tree_rows()), 2)

        self.table.clear()
        self.assertEqual(len(self._tree_rows()), 0)

    def test_get_decisions_returns_strategies(self):
        fs1 = _make_fs('010001', ['.mp4', '.lrv', '.thm'], has_gps=True)
        fs2 = _make_fs('010002', ['.mp4'], has_gps=False)
        ar = AnalysisResult(directory='/d', sets=[fs1, fs2])
        self.table.load_analysis(ar)

        decisions = self.table.get_decisions()
        self.assertIn('010001', decisions)
        self.assertIn('010002', decisions)
        # 010001 has GPS → defaults to gps
        self.assertEqual(decisions['010001']['strategy'], 'gps')
        # 010002 has no GPS → defaults to manual
        self.assertEqual(decisions['010002']['strategy'], 'manual')

    def test_set_manual_delta_updates_tree(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=False)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)
        self.table.manual_delta = timedelta(hours=5)
        self.root.update_idletasks()

        # strategy should still be 'manual'
        rows = self._tree_rows()
        parent = rows[0]
        self.assertEqual(parent[2][6], 'manual')

    def test_set_strategy_updates_decision(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=True)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        self.assertEqual(self.table.decisions['010001'].strategy, 'gps')

        self.table.set_strategy_for_set('010001', STRATEGY_SKIP)
        self.assertEqual(self.table.decisions['010001'].strategy, 'skip')

        self.table.set_strategy_for_set('010001', STRATEGY_MANUAL)
        self.assertEqual(self.table.decisions['010001'].strategy, 'manual')

    def test_set_strategy_updates_treeview_display(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=True)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        self.assertEqual(rows[0][2][6], 'gps')

        self.table.set_strategy_for_set('010001', STRATEGY_SKIP)
        self.root.update_idletasks()
        rows = self._tree_rows()
        self.assertEqual(rows[0][2][6], 'skip')

    def test_status_shows_analysis_summary(self):
        fs1 = _make_fs('010001', ['.mp4', '.lrv'])
        fs2 = _make_fs('010002', ['.mp4', '.lrv', '.thm'])
        ar = AnalysisResult(directory='/d', sets=[fs1, fs2])
        self.table.load_analysis(ar)
        self.assertIn('2 sets', self.table._status_var.get())
        self.assertIn('5 files', self.table._status_var.get())

    def test_mixed_gps_availability(self):
        fs1 = _make_fs('010001', ['.mp4'], has_gps=True)
        fs2 = _make_fs('010002', ['.mp4'], has_gps=False)
        ar = AnalysisResult(directory='/d', sets=[fs1, fs2])
        self.table.load_analysis(ar)

        # Find parents by checking for empty second column (parent rows)
        parents = [(iid, vals) for iid, parent, vals in self._tree_rows() if not parent]
        self.assertEqual(len(parents), 2)
        self.assertEqual(parents[0][1][0], '010001')
        self.assertEqual(parents[0][1][6], 'gps')
        self.assertEqual(parents[1][1][0], '010002')
        self.assertEqual(parents[1][1][6], 'manual')

    def test_timezone_suffixes_on_cell_values(self):
        fs = _make_fs('010001', ['.mp4', '.lrv', '.thm'])
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        # Child rows only (index 1,2,3 = mp4, lrv, thm)
        children = [(iid, vals) for iid, p, vals in self._tree_rows() if p]

        for iid, vals in children:
            mtime = vals[3]  # FS mtime column
            exif = vals[4]   # EXIF time column
            gps = vals[5]    # GPS time column
            target = vals[7]  # Target column
            ext = vals[2]    # file type

            # FS mtime: should have a timezone suffix (current system TZ, e.g. CEST)
            self.assertRegex(mtime,
                r'\d{2}:\d{2}:\d{2} [A-Z]{2,}$',
                f'FS mtime should have timezone suffix, got: {mtime}')

            # GPS time for MP4/LRV: must contain UTC suffix
            if ext in ('mp4', 'lrv'):
                self.assertIn('UTC', gps.upper(), f'GPS time should contain UTC: {gps}')
            else:
                self.assertEqual(gps, '\u2014')

            # EXIF time for MP4/LRV: should have a timezone suffix (CET/CEST)
            if ext in ('mp4', 'lrv'):
                self.assertRegex(exif,
                    r'\d{2}:\d{2}:\d{2} [A-Z]{3,}$',
                    f'EXIF time should have timezone suffix: {exif}')
                # Target should also have timezone suffix
                self.assertRegex(target,
                    r'\d{2}:\d{2}:\d{2} [A-Z]{3,}$',
                    f'Target should have timezone suffix: {target}')
            else:
                # THM: no embedded time → shows '—'
                self.assertEqual(exif, '\u2014')
                # THM target falls back to target_mtime → has TZ suffix
                self.assertRegex(target,
                    r'\d{2}:\d{2}:\d{2} [A-Z]{2,}$',
                    f'THM target should have timezone suffix: {target}')


if __name__ == '__main__':
    unittest.main()
