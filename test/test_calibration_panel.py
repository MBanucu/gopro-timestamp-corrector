"""Test that the auto calibration result is visible in the GUI editors and delta entry."""
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from pathlib import Path

from shared import HAS_TK

if HAS_TK:
    import tkinter as tk


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestAutoCalibrateGUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        from gui.calibration_panel import CalibrationPanel

        self.logged = []
        self.status = None
        self.delta_result = None

        def log_fn(msg):
            self.logged.append(msg)

        def status_fn(msg):
            self.status = msg

        def delta_cb(delta):
            self.delta_result = delta

        self.panel = CalibrationPanel(
            self.root,
            get_dir_fn=lambda: '/fake/dir',
            log_fn=log_fn,
            set_status_fn=status_fn,
            delta_changed_cb=delta_cb,
        )
        self.panel.pack()
        self.root.update_idletasks()

    # ── Helpers ───────────────────────────────────────────────

    def _mock_media(self, file_infos):
        """file_infos: list of (path, embedded_dt, gps_dt, accuracy)"""
        files = [fi[0] for fi in file_infos]
        batch = {fi[0]: (fi[1], fi[2]) for fi in file_infos}
        accuracy = {fi[0]: fi[3] for fi in file_infos}

        return patch.multiple(
            'media',
            collect=MagicMock(return_value=files),
            read_tags_batch=MagicMock(return_value=batch),
            read_gps_accuracy_batch=MagicMock(return_value=accuracy),
        )

    # ── Tests ─────────────────────────────────────────────────

    def _run_auto_calibrate(self):
        with patch.object(self.panel, '_auto_gps') as mock_fallback, \
             patch('pathlib.Path.is_dir', return_value=True):
            self.panel.auto_calibrate()
            self.root.update_idletasks()
            return mock_fallback

    def test_auto_calibrate_sets_delta_entry(self):
        """The computed weighted median delta appears in the Delta tab entry."""
        files = [
            (Path('/d/GL010063.LRV'),
             datetime(2026, 5, 14, 14, 52, 0),
             datetime(2021, 3, 11, 12, 51, 0, 199000),
             2.0),
            (Path('/d/GL010064.LRV'),
             datetime(2026, 5, 14, 15, 2, 27),
             datetime(2026, 5, 14, 13, 2, 27, 400000),
             3.0),
        ]
        # Deltas: ~1891d -21h -59m, ~-1h -59m -59.6s
        # Weighted median should be near the larger (063) delta

        with self._mock_media(files):
            fallback = self._run_auto_calibrate()

        self.assertFalse(fallback.called, 'Should not fall back to single GPS')
        self.assertIn('Auto calibrate', ' '.join(self.logged))
        self.assertIsNotNone(self.delta_result)

        entry_text = self.panel.delta_entry.get()
        self.assertTrue(entry_text.startswith('-'),
                        f'Expected negative delta, got: {entry_text}')
        # At least one unit component should be present
        self.assertTrue(any(c in entry_text for c in 'dhms'),
                        f'Delta entry should contain time units, got: {entry_text}')

    def test_auto_calibrate_sets_calendar_editors(self):
        """The GPS and embedded times of the representative file appear in the Calendar tab."""
        files = [
            (Path('/d/GL010063.LRV'),
             datetime(2026, 5, 14, 14, 52, 0),
             datetime(2021, 3, 11, 12, 51, 0, 199000),
             1.5),
        ]
        with self._mock_media(files):
            fallback = self._run_auto_calibrate()

        self.assertFalse(fallback.called)

        # Gopro editor should be set to the embedded time
        gopro = self.panel.gopro_editor.get_data()
        self.assertEqual(gopro.get('date'), '2026-05-14')
        self.assertEqual(gopro.get('time'), '14:52:00.000')

        # Actual editor date should reflect GPS time
        actual = self.panel.actual_editor.get_data()
        self.assertEqual(actual.get('date'), '2021-03-11')

    def test_auto_calibrate_filters_no_fix_files(self):
        """Files with GPSHPositioningError = 99.99 (no fix) are excluded."""
        good_file = Path('/d/GL010063.LRV')
        bad_file = Path('/d/GL010064.LRV')

        with patch.multiple(
            'media',
            collect=MagicMock(return_value=[good_file, bad_file]),
            read_tags_batch=MagicMock(return_value={
                good_file: (datetime(2026, 5, 14, 14, 52, 0),
                            datetime(2021, 3, 11, 12, 51, 0)),
                bad_file: (datetime(2026, 5, 14, 15, 2, 27),
                           datetime(2026, 5, 14, 13, 2, 27)),
            }),
            read_gps_accuracy_batch=MagicMock(return_value={
                good_file: 2.0,
                bad_file: 99.99,
            }),
        ):
            with patch.object(self.panel, '_auto_gps') as fallback, \
                 patch('pathlib.Path.is_dir', return_value=True):
                self.panel.auto_calibrate()
                self.root.update_idletasks()

        self.assertFalse(fallback.called,
                         'Should not fall back when at least one fix is valid')
        self.assertIsNotNone(self.delta_result)
        self.assertIn('1 files with valid GPS fix', self.logged[0])

    def test_auto_calibrate_falls_back_on_no_valid_fix(self):
        """When all files have no fix, fall back to single GPS extraction."""
        f = Path('/d/GL010063.LRV')
        with patch.multiple(
            'media',
            collect=MagicMock(return_value=[f]),
            read_tags_batch=MagicMock(return_value={
                f: (datetime(2026, 5, 14, 14, 52, 0),
                    datetime(2021, 3, 11, 12, 51, 0)),
            }),
            read_gps_accuracy_batch=MagicMock(return_value={f: 99.99}),
        ):
            with patch.object(self.panel, '_auto_gps') as fallback, \
                 patch('pathlib.Path.is_dir', return_value=True):
                self.panel.auto_calibrate()
                self.root.update_idletasks()

        fallback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
