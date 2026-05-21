"""Test that the auto calibration result is visible in the GUI editors and delta entry."""
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from pathlib import Path

from exiftool_session import ExifToolSession
from shared import HAS_TK

if HAS_TK:
    import tkinter as tk


def _mock_session(file_infos):
    """file_infos: list of (path, embedded_dt, gps_dt, accuracy)"""
    files = [fi[0] for fi in file_infos]
    batch = {fi[0]: (fi[1], fi[2]) for fi in file_infos}
    accuracy = {fi[0]: fi[3] for fi in file_infos}

    session = MagicMock(spec=ExifToolSession)
    session.read_tags_batch.return_value = batch
    session.read_gps_accuracy_batch.return_value = accuracy
    return session


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
        self.session = MagicMock(spec=ExifToolSession)

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
            session=self.session,
        )
        self.panel.pack()
        self.root.update_idletasks()

    # ── Tests ─────────────────────────────────────────────────

    def _setup_session_mock(self, file_infos):
        """Configure self.session mock with given file_infos.
        
        file_infos: list of (path, embedded_dt, gps_dt, accuracy)
        """
        files = [fi[0] for fi in file_infos]
        batch = {fi[0]: (fi[1], fi[2]) for fi in file_infos}
        accuracy = {fi[0]: fi[3] for fi in file_infos}
        self.session.read_tags_batch.return_value = batch
        self.session.read_gps_accuracy_batch.return_value = accuracy

    def _run_auto_calibrate(self, files):
        with patch.object(self.panel, '_auto_gps') as mock_fallback, \
             patch('media.collect', return_value=files), \
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
        self._setup_session_mock(files)
        file_paths = [f[0] for f in files]
        fallback = self._run_auto_calibrate(file_paths)

        self.assertFalse(fallback.called, 'Should not fall back to single GPS')
        self.assertIn('Auto calibrate', ' '.join(self.logged))
        self.assertIsNotNone(self.delta_result)

        self.assertEqual(self.panel.delta_sign_var.get(), '-')
        has_value = any(int(v.get() or '0') > 0
                        for v in (self.panel.day_var, self.panel.hour_var,
                                  self.panel.min_var, self.panel.sec_var,
                                  self.panel.ms_var))
        self.assertTrue(has_value, 'At least one spinbox should be non-zero')

    def test_auto_calibrate_sets_calendar_editors(self):
        """The GPS and embedded times of the representative file appear in the Calendar tab."""
        files = [
            (Path('/d/GL010063.LRV'),
             datetime(2026, 5, 14, 14, 52, 0),
             datetime(2021, 3, 11, 12, 51, 0, 199000),
             1.5),
        ]
        self._setup_session_mock(files)
        file_paths = [f[0] for f in files]
        fallback = self._run_auto_calibrate(file_paths)

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
        self._setup_session_mock([
            (good_file, datetime(2026, 5, 14, 14, 52, 0),
             datetime(2021, 3, 11, 12, 51, 0), 2.0),
            (bad_file, datetime(2026, 5, 14, 15, 2, 27),
             datetime(2026, 5, 14, 13, 2, 27), 99.99),
        ])

        with patch.object(self.panel, '_auto_gps') as fallback, \
             patch('media.collect', return_value=[good_file, bad_file]), \
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
        self._setup_session_mock([
            (f, datetime(2026, 5, 14, 14, 52, 0),
             datetime(2021, 3, 11, 12, 51, 0), 99.99),
        ])

        with patch.object(self.panel, '_auto_gps') as fallback, \
             patch('media.collect', return_value=[f]), \
             patch('pathlib.Path.is_dir', return_value=True):
            self.panel.auto_calibrate()
            self.root.update_idletasks()

        fallback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
