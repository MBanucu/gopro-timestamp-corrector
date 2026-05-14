import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import AnalysisResult, FileSet, FileInfo
from preview import (
    compute_preview, SetDecision, FilePreview,
    _gps_delta_for_set, _gps_preview, _manual_preview, _skip_preview,
    STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP,
)


def _make_fs(set_id, ext_list, gps_times=None, emb_times=None, mtime=None):
    files = []
    now = datetime(2026, 5, 14, 18, 0, 0)
    base_mtime = mtime or now
    for i, ext in enumerate(ext_list):
        prefix = 'GL' if ext == '.lrv' else 'GX'
        stem = f'{prefix}0{set_id}'
        gps = gps_times[i] if gps_times and i < len(gps_times) else None
        emb = emb_times[i] if emb_times and i < len(emb_times) else None
        fi = FileInfo(
            path=Path(f'/d/{stem}{ext}'),
            stem=stem,
            ext=ext,
            mtime=base_mtime,
            embedded_time=emb,
            gps_time=gps,
        )
        files.append(fi)
    return FileSet(id=set_id, files=files)


class TestGpsDeltaForSet(unittest.TestCase):
    def test_gps_delta_computed(self):
        gps_utc = datetime(2026, 5, 14, 13, 0, 0)
        emb = datetime(2026, 5, 14, 17, 0, 0)
        fs = _make_fs('010001', ['.mp4', '.lrv'],
                        gps_times=[gps_utc, gps_utc],
                        emb_times=[emb, emb])

        delta = _gps_delta_for_set(fs)
        self.assertIsNotNone(delta)
        # GPS UTC = 13:00, local (CEST) = 15:00, emb = 17:00
        # delta = 15:00 - 17:00 = -2h
        self.assertEqual(delta, timedelta(hours=-2))

    def test_no_gps_returns_none(self):
        fs = _make_fs('010001', ['.mp4'], gps_times=[None], emb_times=[datetime(2026, 5, 14, 17, 0, 0)])
        delta = _gps_delta_for_set(fs)
        self.assertIsNone(delta)

    def test_no_embedded_returns_none(self):
        fs = _make_fs('010001', ['.mp4'], gps_times=[datetime(2026, 5, 14, 13, 0, 0)], emb_times=[None])
        delta = _gps_delta_for_set(fs)
        self.assertIsNone(delta)


class TestComputePreview(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 5, 14, 18, 0, 0)
        emb1 = datetime(2026, 5, 14, 16, 0, 0)
        gps1 = datetime(2026, 5, 14, 12, 0, 0)

        fs1 = _make_fs('010001', ['.mp4', '.lrv', '.thm'],
                        gps_times=[gps1, gps1, None],
                        emb_times=[emb1, emb1, None],
                        mtime=now)
        fs2 = _make_fs('010002', ['.mp4'],
                        gps_times=[None],
                        emb_times=[emb1],
                        mtime=now)

        self.analysis = AnalysisResult(directory='/d', sets=[fs1, fs2])

    def test_skip_strategy(self):
        decisions = {
            '010001': SetDecision(strategy=STRATEGY_SKIP),
            '010002': SetDecision(strategy=STRATEGY_SKIP),
        }
        results = compute_preview(self.analysis, decisions)
        for pr in results:
            for fp in pr.file_results:
                self.assertEqual(fp.current_embedded, fp.target_embedded)
                self.assertEqual(fp.current_mtime, fp.target_mtime)

    def test_manual_strategy(self):
        delta = timedelta(hours=2, minutes=30)
        decisions = {
            '010001': SetDecision(strategy=STRATEGY_MANUAL, manual_delta=delta),
            '010002': SetDecision(strategy=STRATEGY_MANUAL, manual_delta=delta),
        }
        results = compute_preview(self.analysis, decisions)
        for pr in results:
            for fp in pr.file_results:
                if fp.current_embedded:
                    self.assertEqual(fp.target_embedded, fp.current_embedded + delta)
                if fp.current_mtime:
                    self.assertEqual(fp.target_mtime, fp.current_mtime + delta)

    def test_manual_uses_global_delta_fallback(self):
        global_delta = timedelta(hours=1)
        decisions = {
            '010001': SetDecision(strategy=STRATEGY_MANUAL),  # no manual_delta set
        }
        results = compute_preview(self.analysis, decisions, global_manual_delta=global_delta)
        for pr in results:
            for fp in pr.file_results:
                if fp.current_embedded:
                    self.assertEqual(fp.target_embedded, fp.current_embedded + global_delta)

    def test_gps_strategy(self):
        decisions = {
            '010001': SetDecision(strategy=STRATEGY_GPS),
            '010002': SetDecision(strategy=STRATEGY_GPS),
        }
        results = compute_preview(self.analysis, decisions)
        pr = next(r for r in results if r.set_id == '010001')
        for fp in pr.file_results:
            if fp.current_embedded:
                # GPS UTC=12:00 local=CEST=14:00, emb=16:00, delta=-2h
                self.assertEqual(fp.target_embedded, fp.current_embedded + timedelta(hours=-2))

    def test_gps_strategy_thm_fallback(self):
        decisions = {'010001': SetDecision(strategy=STRATEGY_GPS)}
        results = compute_preview(self.analysis, decisions)
        pr = next(r for r in results if r.set_id == '010001')
        thm = next(fp for fp in pr.file_results if fp.path.suffix == '.thm')
        # THM has no embedded but has mtime; should still get delta applied
        self.assertIsNotNone(thm.target_mtime)
        self.assertNotEqual(thm.current_mtime, thm.target_mtime)

    def test_gps_no_data_falls_back_to_skip(self):
        decisions = {'010002': SetDecision(strategy=STRATEGY_GPS)}
        results = compute_preview(self.analysis, decisions)
        pr = next(r for r in results if r.set_id == '010002')
        # Set 010002 has no GPS data, should fallback to skip (no change)
        for fp in pr.file_results:
            self.assertEqual(fp.current_embedded, fp.target_embedded)

    def test_result_structure(self):
        decisions = {
            '010001': SetDecision(strategy=STRATEGY_GPS),
            '010002': SetDecision(strategy=STRATEGY_SKIP),
        }
        results = compute_preview(self.analysis, decisions)
        self.assertEqual(len(results), 2)
        for pr in results:
            self.assertIn(pr.set_id, ['010001', '010002'])
            self.assertIn(pr.strategy, [STRATEGY_GPS, STRATEGY_SKIP])
            for fp in pr.file_results:
                self.assertIsInstance(fp, FilePreview)
                self.assertIsInstance(fp.path, Path)

    def test_preview_filepreview_fields(self):
        decisions = {'010001': SetDecision(strategy=STRATEGY_MANUAL, manual_delta=timedelta(hours=1))}
        results = compute_preview(self.analysis, decisions)
        fp = results[0].file_results[0]
        self.assertIsNotNone(fp.current_embedded)
        self.assertIsNotNone(fp.current_mtime)
        self.assertIsNotNone(fp.target_embedded)
        self.assertIsNotNone(fp.target_mtime)

    def test_default_strategy_is_manual(self):
        decisions = {}  # no decisions -> defaults to manual
        results = compute_preview(self.analysis, decisions, global_manual_delta=timedelta(hours=1))
        for pr in results:
            self.assertEqual(pr.strategy, 'manual')


if __name__ == '__main__':
    unittest.main()
