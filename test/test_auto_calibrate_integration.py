"""Integration test: auto calibrate against the real files on the mounted sdcard image."""
import subprocess
import re
import shutil
import unittest
from datetime import timezone
from pathlib import Path

from shared import HAS_TK, prepare_sparse_image, setup_loop_device, teardown_loop_device

if HAS_TK:
    import tkinter as tk


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestAutoCalibrateIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._work_dir = None
        cls.mount_point = None
        cls.loop_dev = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')
        cls._work_dir, cls.img_path = prepare_sparse_image(gz_path)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'Expected directory {cls.target} not found')
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

    @classmethod
    def tearDownClass(cls):
        teardown_loop_device(cls.loop_dev, cls.mount_point)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    # ── Helpers ───────────────────────────────────────────────

    def _collect_gps_pairs(self, session=None):
        """Return (pairs, median) from the real files on the mounted image."""
        import media
        files = media.collect(self.target)
        if session:
            batch = session.read_tags_batch(files)
            accuracy = session.read_gps_accuracy_batch(files)
        else:
            from exiftool_session import ExifToolSession
            with ExifToolSession(connect=None) as s:
                batch = s.read_tags_batch(files)
                accuracy = s.read_gps_accuracy_batch(files)
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
            delta = gps - embedded
            weight = 1.0 / (acc + 1.0)
            pairs.append((delta, weight, f, gps, embedded))
        self.assertGreaterEqual(
            len(pairs), 1,
            'At least one file on the image should have a valid GPS fix')
        from resolve import weighted_median_delta
        deltas = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        median = weighted_median_delta(deltas, weights)
        self.assertIsNotNone(median, 'Weighted median delta should be computable')
        return pairs, median

    def _make_panel(self, session=None):
        """Create a CalibrationPanel with spy callbacks."""
        from gui.calibration_panel import CalibrationPanel
        logged = []
        status = []
        delta_result = []
        panel = CalibrationPanel(
            self.root,
            get_dir_fn=lambda: str(self.target),
            log_fn=logged.append,
            set_status_fn=status.append,
            delta_changed_cb=delta_result.append,
            session=session,
        )
        panel.pack()
        self.root.update_idletasks()
        return panel, logged, delta_result

    # ── Tests ─────────────────────────────────────────────────

    def _assert_editors_match(self, panel, pairs, median):
        """Verify that the calendar editors show the representative file's
        actual (GPS-to-local) and gopro (embedded) times."""
        actual = panel.actual_editor.get_data()
        gopro = panel.gopro_editor.get_data()

        tz_id = panel.actual_editor.tz_var.get()
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None

        # The actual editor shows GPS in local timezone; the gopro editor
        # shows embedded time as UTC (QuickTime dates are UTC per spec).
        best = min(pairs, key=lambda c: abs((c[0] - median).total_seconds()))
        _, _, best_file, best_gps, best_emb = best

        gps_utc_tz = best_gps.replace(tzinfo=timezone.utc)
        expected_actual_dt = gps_utc_tz.astimezone(tz) if tz else gps_utc_tz.astimezone()

        # Both editors show times in the same local timezone
        gps_local = best_gps.astimezone(tz) if tz else best_gps.astimezone()
        emb_local = best_emb.astimezone(tz) if tz else best_emb.astimezone()
        self.assertEqual(actual.get('date'), gps_local.strftime('%Y-%m-%d'))
        self.assertEqual(actual.get('time'), gps_local.strftime('%H:%M:%S.') + f"{gps_local.microsecond//1000:03d}")
        self.assertEqual(actual.get('timezone'), tz_id)
        self.assertEqual(gopro.get('date'), emb_local.strftime('%Y-%m-%d'))
        self.assertEqual(gopro.get('time'), emb_local.strftime('%H:%M:%S.') + f"{emb_local.microsecond//1000:03d}")
        self.assertEqual(gopro.get('timezone'), actual.get('timezone'))
        return best_file, gps_local, emb_local

    def test_auto_calibrate_editors_populated(self):
        """The calendar editors show the representative file's GPS and embedded times."""
        from exiftool_session import ExifToolSession
        with ExifToolSession(connect=None) as session:
            panel, logged, delta_result = self._make_panel(session=session)
            pairs, median = self._collect_gps_pairs(session)

            panel.auto_calibrate()
            self.root.update_idletasks()

            best_file, actual_dt, emb_dt = self._assert_editors_match(panel, pairs, median)

            for msg in logged:
                print(f'  [log] {msg}')
            print(f'  Representative: {best_file.name}')
            print(f'  Actual: {actual_dt}')
            print(f'  GoPro:  {emb_dt}')

    def test_auto_calibrate_respects_timezone(self):
        """With a timezone configured, the actual editor shows GPS time in that zone."""
        from exiftool_session import ExifToolSession
        with ExifToolSession(connect=None) as session:
            panel, logged, delta_result = self._make_panel(session=session)

            panel.actual_editor.tz_var.set('Europe/Berlin')
            self.root.update_idletasks()

            pairs, median = self._collect_gps_pairs(session)

            panel.auto_calibrate()
            self.root.update_idletasks()

            best_file, actual_dt, emb_dt = self._assert_editors_match(panel, pairs, median)

            import zoneinfo
            berlin = zoneinfo.ZoneInfo('Europe/Berlin')
            best_pair = min(pairs, key=lambda c: abs((c[0] - median).total_seconds()))
            _, _, best_file, best_gps, best_emb = best_pair
            expected_local = best_gps.replace(tzinfo=timezone.utc).astimezone(berlin)

            self.assertEqual(panel.actual_editor.tz_var.get(), 'Europe/Berlin')
            self.assertEqual(panel.gopro_editor.tz_var.get(), 'Europe/Berlin')

            self.assertEqual(actual_dt.year, expected_local.year)
            self.assertEqual(actual_dt.month, expected_local.month)
            self.assertEqual(actual_dt.day, expected_local.day)
            self.assertEqual(actual_dt.hour, expected_local.hour)
            self.assertEqual(actual_dt.minute, expected_local.minute)
            self.assertEqual(expected_local.utcoffset().total_seconds() / 3600, 2.0)

            for msg in logged:
                print(f'  [log] {msg}')
            print(f'  TZ: Europe/Berlin (CEST, UTC+2)')
            print(f'  Representative: {best_file.name}')
            print(f'  GPS (UTC): {best_gps}')
            print(f'  Actual (CEST): {actual_dt}')
            print(f'  GoPro:  {emb_dt}')

    def test_auto_calibrate_delta_is_median(self):
        """The delta callback receives the weighted median, not a per-file delta.

        With GPS time correctly converted to local timezone before delta
        computation, the camera-clock error for files 064-066 should be
        near zero (sub-second precision), not dominated by the -2 h
        timezone offset.
        """
        from exiftool_session import ExifToolSession
        with ExifToolSession(connect=None) as session:
            panel, logged, delta_result = self._make_panel(session=session)
            pairs, median = self._collect_gps_pairs(session)

            panel.auto_calibrate()
            self.root.update_idletasks()

            self.assertGreater(len(delta_result), 0, 'Delta callback should fire')
            self.assertAlmostEqual(delta_result[-1].total_seconds(),
                                   median.total_seconds(), delta=1.0)

            has_value = any(int(v.get() or '0') > 0
                            for v in (panel.day_var, panel.hour_var,
                                      panel.min_var, panel.sec_var,
                                      panel.ms_var))
            self.assertTrue(has_value, 'At least one spinbox should be non-zero')
            self.assertEqual(panel.delta_sign_var.get(), '-')

            print(f'  Delta sign: {panel.delta_sign_var.get()}')
            print(f'  Computed median: {median}')
            print(f'  Delta callback value: {delta_result[-1].total_seconds():.2f} s')


if __name__ == '__main__':
    unittest.main()
