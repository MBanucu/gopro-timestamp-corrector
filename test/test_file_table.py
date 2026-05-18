"""Tests for the FileSetTable GUI widget."""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import HAS_TK
from analysis import AnalysisResult, FileSet, FileInfo
from preview import SetDecision, STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP

if HAS_TK:
    import tkinter as tk
    from tkinter import ttk
    from gui.file_table import FileSetTable


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


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
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

    def test_plan_is_none_before_analysis(self):
        self.assertIsNone(self.table.plan)
        with self.assertRaises(AttributeError):
            _ = self.table.analysis  # guard in app.py must use .plan, not .analysis

    def test_load_analysis_creates_rows(self):
        fs = _make_fs('010001', ['.mp4', '.lrv', '.thm'])
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        self.assertEqual(len(rows), 4)  # 1 parent + 3 children
        parent = rows[0]
        self.assertEqual(parent[2][0], '010001')  # set ID in first column
        self.assertEqual(parent[2][8], 'manual')   # strategy = MANUAL (even if has GPS)

    def test_load_analysis_no_gps_defaults_to_manual(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=False)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        parent = rows[0]
        self.assertEqual(parent[2][8], 'manual')

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
        # Both should default to manual
        self.assertEqual(decisions['010001']['strategy'], 'manual')
        self.assertEqual(decisions['010002']['strategy'], 'manual')

    def test_set_manual_delta_updates_tree(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=False)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)
        self.table.manual_delta = timedelta(hours=5)
        self.root.update_idletasks()

        rows = self._tree_rows()
        parent = rows[0]
        self.assertEqual(parent[2][8], 'manual')

    def test_set_strategy_updates_decision(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=True)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        decs = self.table.plan.decisions
        self.assertEqual(decs['010001'].strategy, 'manual')

        self.table.set_strategy_for_set('010001', STRATEGY_GPS)
        self.assertEqual(decs['010001'].strategy, 'gps')

        self.table.set_strategy_for_set('010001', STRATEGY_SKIP)
        self.assertEqual(decs['010001'].strategy, 'skip')

    def test_set_strategy_updates_treeview_display(self):
        fs = _make_fs('010001', ['.mp4'], has_gps=True)
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)

        rows = self._tree_rows()
        self.assertEqual(rows[0][2][8], 'manual')

        self.table.set_strategy_for_set('010001', STRATEGY_GPS)
        self.root.update_idletasks()
        rows = self._tree_rows()
        self.assertEqual(rows[0][2][8], 'gps')

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
        self.assertEqual(parents[0][1][8], 'manual')
        self.assertEqual(parents[1][1][0], '010002')
        self.assertEqual(parents[1][1][8], 'manual')

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
            target = vals[9]  # Target column
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


    def test_thm_target_shows_dst_from_zoneinfo(self):
        from gui.tz_info import get_iana_id as _get_iana_id
        from gui.file_table import _utc_to_local_with_tz
        import zoneinfo

        iana = _get_iana_id()
        if not iana:
            self.skipTest('IANA timezone not detected')

        # Simulate GX010063.THM: mtime and target are now both in UTC
        may_mtime_utc = datetime(2026, 5, 14, 19, 6, 18)  # UTC
        delta = timedelta(days=-1891, hours=-21, minutes=-59, seconds=0, microseconds=-199000)
        target_utc = may_mtime_utc + delta  # lands in March UTC

        target_local, tz_suffix = _utc_to_local_with_tz(target_utc)
        self.assertIsNotNone(target_local)
        self.assertTrue(tz_suffix, 'THM target must have a timezone suffix from zoneinfo')

        # zoneinfo for a March date in Europe/Berlin must produce CET
        if iana == 'Europe/Berlin':
            z = zoneinfo.ZoneInfo('Europe/Berlin')
            target_aware = target_utc.replace(tzinfo=timezone.utc).astimezone(z)
            self.assertEqual(target_aware.tzname(), 'CET',
                             f'March target should be CET, got {target_aware.tzname()}')

    def test_thm_target_in_gui_shows_cet_for_march(self):
        from gui.tz_info import get_iana_id as _get_iana_id
        iana = _get_iana_id()
        if iana != 'Europe/Berlin':
            self.skipTest(f'Test requires Europe/Berlin, got {iana}')

        # Create a set with GPS delta that pushes THM target to March (CET)
        now_may = datetime(2026, 5, 14, 18, 0, 0)
        gps_march = datetime(2021, 3, 11, 12, 0, 0)  # UTC
        emb_may = datetime(2026, 5, 14, 16, 0, 0)     # UTC

        mp4 = FileInfo(
            path=Path('/d/GX010063.MP4'), stem='GX010063', ext='.mp4',
            mtime=now_may, embedded_time=emb_may, gps_time=gps_march)
        lrv = FileInfo(
            path=Path('/d/GL010063.LRV'), stem='GL010063', ext='.lrv',
            mtime=now_may, embedded_time=emb_may, gps_time=gps_march)
        thm = FileInfo(
            path=Path('/d/GX010063.THM'), stem='GX010063', ext='.thm',
            mtime=now_may, embedded_time=None, gps_time=None)

        fs = FileSet(id='010063', files=[mp4, lrv, thm])
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)
        self.table.set_strategy_for_set('010063', STRATEGY_GPS)
        self.root.update_idletasks()

        children = [(iid, vals) for iid, p, vals in self._tree_rows() if p]
        thm_row = next((vals for iid, vals in children if vals[2] == 'thm'), None)
        self.assertIsNotNone(thm_row, 'THM row not found')

        target = thm_row[9]  # Target column
        self.assertIn('CET', target.upper(),
                      f'THM target for March should show CET: {target}')
        self.assertNotIn('CEST', target.upper(),
                         f'THM target for March should NOT show CEST: {target}')

    def test_thm_target_equals_mp4_lrv_with_gps_strategy(self):
        from gui.tz_info import get_iana_id as _get_iana_id
        iana = _get_iana_id()
        if iana != 'Europe/Berlin':
            self.skipTest(f'Test requires Europe/Berlin, got {iana}')

        now = datetime(2026, 5, 14, 18, 0, 0)  # UTC
        gps_utc = datetime(2021, 3, 11, 12, 0, 0)
        emb_utc = datetime(2026, 5, 14, 16, 0, 0)

        mp4 = FileInfo(path=Path('/d/GX010001.MP4'), stem='GX010001', ext='.mp4',
                       mtime=now, embedded_time=emb_utc, gps_time=gps_utc)
        lrv = FileInfo(path=Path('/d/GL010001.LRV'), stem='GL010001', ext='.lrv',
                       mtime=now, embedded_time=emb_utc, gps_time=gps_utc)
        thm = FileInfo(path=Path('/d/GX010001.THM'), stem='GX010001', ext='.thm',
                       mtime=now, embedded_time=None, gps_time=None)

        fs = FileSet(id='010001', files=[mp4, lrv, thm])
        ar = AnalysisResult(directory='/d', sets=[fs])
        self.table.load_analysis(ar)
        self.table.set_strategy_for_set('010001', STRATEGY_GPS)
        self.root.update_idletasks()

        children = [(iid, vals) for iid, p, vals in self._tree_rows() if p]
        targets = {}
        for iid, vals in children:
            ext = vals[2]
            target = vals[9]
            targets[ext] = target

        # All three must have a target with timezone suffix
        for ext in ('mp4', 'lrv', 'thm'):
            self.assertIn(ext, targets, f'Missing row for {ext}')
            self.assertRegex(targets[ext],
                r'\d{2}:\d{2}:\d{2} [A-Z]{2,}$',
                f'{ext} target should have timezone suffix: {targets[ext]}')

        # THM target must match MP4 and LRV
        self.assertEqual(targets['thm'], targets['mp4'],
                         'THM target should equal MP4 target')
        self.assertEqual(targets['thm'], targets['lrv'],
                         'THM target should equal LRV target')


if __name__ == '__main__':
    unittest.main()
