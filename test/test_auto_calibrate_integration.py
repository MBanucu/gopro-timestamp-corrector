"""Integration test: auto calibrate against the real files on the mounted sdcard image."""
import subprocess
import re
import shutil
import tkinter as tk
import unittest
from datetime import timezone
from pathlib import Path

from shared import decompress_sparse_image


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

    # ── Tests ─────────────────────────────────────────────────

    def test_auto_calibrate_against_real_files(self):
        """Run auto calibration on the mounted image and verify the delta and editors."""
        import media
        from gui.calibration_panel import CalibrationPanel

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

        self.assertGreaterEqual(len(pairs), 1,
                                'At least one file on the image should have a valid GPS fix')

        # Compute weighted median
        from resolve import weighted_median_delta
        deltas = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        median = weighted_median_delta(deltas, weights)

        self.assertIsNotNone(median, 'Weighted median delta should be computable')

        # Now create the CalibrationPanel and run auto calibrate
        logged = []
        status = []
        delta_result = []

        def log_fn(msg):
            logged.append(msg)

        def status_fn(msg):
            status.append(msg)

        def delta_cb(delta):
            delta_result.append(delta)

        panel = CalibrationPanel(
            self.root,
            get_dir_fn=lambda: str(self.target),
            log_fn=log_fn,
            set_status_fn=status_fn,
            delta_changed_cb=delta_cb,
        )
        panel.pack()
        self.root.update_idletasks()

        # Run auto calibration through the panel
        panel._auto_calibrate_from_gps()
        self.root.update_idletasks()

        # Check the delta entry has the expected weighted median
        entry_text = panel.delta_entry.get()
        self.assertNotEqual(entry_text, '', 'Delta entry should not be empty after auto calibrate')
        self.assertTrue(any(c in entry_text for c in 'dhms'),
                        f'Delta entry should contain time units, got: {entry_text}')

        # Check the delta callback was called
        self.assertGreater(len(delta_result), 0,
                           'Delta callback should have been called')
        if delta_result:
            self.assertIsNotNone(delta_result[-1])

        # Check that the calendar editors were populated
        actual = panel.actual_editor.get_data()
        gopro = panel.gopro_editor.get_data()

        # Determine the representative file (same logic as in _auto_calibrate_from_gps)
        tz_id = panel.actual_editor.tz_var.get()
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_id) if tz_id else None
        except Exception:
            tz = None

        best = min(pairs, key=lambda c: abs((c[0] - median).total_seconds()))
        _, _, best_file, best_gps, best_emb = best

        # Actual editor should show the GPS time of the representative file,
        # converted to the local timezone.
        gps_utc_tz = best_gps.replace(tzinfo=timezone.utc)
        expected_actual_dt = gps_utc_tz.astimezone(tz) if tz else gps_utc_tz.astimezone()
        self.assertEqual(actual.get('date'), expected_actual_dt.strftime('%Y-%m-%d'))
        self.assertEqual(actual.get('time'), expected_actual_dt.strftime('%H:%M'))
        self.assertEqual(actual.get('timezone', ''), tz_id)

        # GoPro editor should show the embedded time of the representative file,
        # without any timezone (camera-local time as stored).
        self.assertEqual(gopro.get('date'), best_emb.strftime('%Y-%m-%d'))
        self.assertEqual(gopro.get('time'), best_emb.strftime('%H:%M'))
        self.assertEqual(gopro.get('timezone', ''), '')

        # The delta callback should have been called with the weighted median.
        self.assertIsNotNone(delta_result[-1], 'Delta callback should have a value')
        self.assertAlmostEqual(delta_result[-1].total_seconds(),
                               median.total_seconds(), delta=1.0,
                               msg='Delta callback value should match weighted median')

        # Log what happened for debugging
        for msg in logged:
            print(f'  [log] {msg}')

        print(f'  Delta entry: {entry_text}')
        print(f'  Representative file: {best_file.name}')
        print(f'  Actual: {actual.get("date")} {actual.get("time")}')
        print(f'  GoPro:  {gopro.get("date")} {gopro.get("time")}')
        print(f'  Computed median: {median}')


if __name__ == '__main__':
    unittest.main()
