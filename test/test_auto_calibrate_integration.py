"""Integration test: auto calibrate against the real files on the mounted sdcard image."""
import subprocess
import re
import shutil
import unittest
from datetime import timezone
from pathlib import Path

from shared import HAS_TK, decompress_sparse_image

if HAS_TK:
    import tkinter as tk


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestAutoCalibrateIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp_dir = None
        cls.mount_point = None
        cls.loop_dev = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')
        img_path = Path(__file__).parent / 'sdcard.img'
        cls.img_path = decompress_sparse_image(gz_path, img_path)

        try:
            r = subprocess.run(
                ['udisksctl', 'loop-setup', '-f', str(cls.img_path),
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise unittest.SkipTest('udisksctl loop-setup failed')
            m = re.search(r'as (/dev/loop\d+)', r.stdout)
            cls.loop_dev = m.group(1) if m else None
            if not cls.loop_dev:
                raise unittest.SkipTest('Could not parse loop device')

            r = subprocess.run(
                ['udisksctl', 'mount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                if 'AlreadyMounted' in r.stderr:
                    m = re.search(r"at `([^`]+)'", r.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                if not cls.mount_point:
                    raise unittest.SkipTest('udisksctl mount failed')
            else:
                m = re.search(r'at ([^ \n]+)', r.stdout)
                if m:
                    cls.mount_point = m.group(1).rstrip('.')
                else:
                    raise unittest.SkipTest('Could not parse mount point')
        except FileNotFoundError:
            raise unittest.SkipTest('udisksctl not found')

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'Expected directory {cls.target} not found')
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

    @classmethod
    def tearDownClass(cls):
        if cls.loop_dev:
            r = subprocess.run(
                ['udisksctl', 'unmount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(['sudo', 'umount', cls.loop_dev],
                               capture_output=True)
            subprocess.run(['sudo', 'losetup', '-d', cls.loop_dev],
                           capture_output=True)
        if cls._temp_dir:
            shutil.rmtree(cls._temp_dir, ignore_errors=True)

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    # ── Helpers ───────────────────────────────────────────────

    def _collect_gps_pairs(self):
        """Return (pairs, median) from the real files on the mounted image."""
        import media
        batch = media.read_tags_batch(media.collect(self.target))
        accuracy = media.read_gps_accuracy_batch(media.collect(self.target))
        pairs = []
        for f in media.collect(self.target):
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

    def _make_panel(self):
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
        panel, logged, delta_result = self._make_panel()
        pairs, median = self._collect_gps_pairs()

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
        panel, logged, delta_result = self._make_panel()

        # Set a timezone before running auto calibration
        panel.actual_editor.tz_var.set('Europe/Berlin')
        self.root.update_idletasks()

        pairs, median = self._collect_gps_pairs()

        panel.auto_calibrate()
        self.root.update_idletasks()

        best_file, actual_dt, emb_dt = self._assert_editors_match(panel, pairs, median)

        # With Europe/Berlin in May (CEST = UTC+2), the local time should be
        # 2 hours ahead of the GPS UTC time.
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
        # Verify the offset is CEST (+2h)
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
        panel, logged, delta_result = self._make_panel()
        pairs, median = self._collect_gps_pairs()

        panel.auto_calibrate()
        self.root.update_idletasks()

        self.assertGreater(len(delta_result), 0, 'Delta callback should fire')
        self.assertAlmostEqual(delta_result[-1].total_seconds(),
                               median.total_seconds(), delta=1.0)

        entry_text = panel.delta_entry.get()
        self.assertNotEqual(entry_text, '')
        self.assertTrue(any(c in entry_text for c in 'dhms'),
                        f'Delta entry should contain time units, got: {entry_text}')

        print(f'  Delta entry: {entry_text}')
        print(f'  Computed median: {median}')
        print(f'  Delta callback value: {delta_result[-1].total_seconds():.2f} s')


if __name__ == '__main__':
    unittest.main()
