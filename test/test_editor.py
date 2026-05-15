"""Tests for the CalibrationEditor widget."""
import unittest
import zoneinfo
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone

from gui.editor import CalibrationEditor


class TestCalibrationEditor(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.frame = ttk.Frame(self.root)
        self.frame.pack()
        self.editor = CalibrationEditor(self.frame, 'Test')
        self.editor.pack()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def _fmt_time(self, hour, minute):
        return f'{hour:02d}:{minute:02d}'

    def test_set_datetime_with_timezone(self):
        """set_datetime extracts the IANA timezone from an aware datetime."""
        berlin = zoneinfo.ZoneInfo('Europe/Berlin')
        dt = datetime(2026, 5, 14, 15, 41, tzinfo=berlin)

        self.editor.set_datetime(dt)
        self.root.update_idletasks()

        data = self.editor.get_data()
        self.assertEqual(data.get('date'), '2026-05-14')
        self.assertEqual(data.get('time'), self._fmt_time(15, 41))
        self.assertEqual(data.get('timezone'), 'Europe/Berlin')

    def test_set_datetime_naive(self):
        """set_datetime with a naive datetime sets date/time and clears timezone."""
        dt = datetime(2026, 3, 11, 8, 30)

        self.editor.set_datetime(dt)
        self.root.update_idletasks()

        data = self.editor.get_data()
        self.assertEqual(data.get('date'), '2026-03-11')
        self.assertEqual(data.get('time'), self._fmt_time(8, 30))
        # The editor should clear the timezone when given a naive datetime
        self.assertIn(data.get('timezone', ''), ('', None))

    def test_set_datetime_utc(self):
        """datetime.timezone.utc is translated to 'UTC'."""
        dt = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)

        self.editor.set_datetime(dt)
        self.root.update_idletasks()

        data = self.editor.get_data()
        self.assertEqual(data.get('date'), '2026-05-14')
        self.assertEqual(data.get('time'), self._fmt_time(12, 0))
        self.assertEqual(data.get('timezone'), 'UTC')

    def test_set_datetime_preserves_previous_tz(self):
        """A naive datetime preserves an already-configured timezone."""
        berlin = zoneinfo.ZoneInfo('Europe/Berlin')
        dt_aware = datetime(2026, 5, 14, 15, 41, tzinfo=berlin)
        self.editor.set_datetime(dt_aware)
        self.root.update_idletasks()

        # Now set with naive datetime — date/time change but timezone stays
        dt_naive = datetime(2026, 3, 11, 8, 30)
        self.editor.set_datetime(dt_naive)
        self.root.update_idletasks()

        data = self.editor.get_data()
        self.assertEqual(data.get('date'), '2026-03-11')
        self.assertEqual(data.get('time'), self._fmt_time(8, 30))
        self.assertEqual(data.get('timezone'), 'Europe/Berlin')

    def test_set_datetime_overrides_timezone(self):
        """An aware datetime overrides the previously set timezone."""
        berlin = zoneinfo.ZoneInfo('Europe/Berlin')
        ny = zoneinfo.ZoneInfo('America/New_York')

        self.editor.set_datetime(datetime(2026, 5, 14, 15, 41, tzinfo=berlin))
        self.root.update_idletasks()

        self.editor.set_datetime(datetime(2026, 3, 11, 8, 30, tzinfo=ny))
        self.root.update_idletasks()

        data = self.editor.get_data()
        self.assertEqual(data.get('timezone'), 'America/New_York')


    def test_tz_abbr_cest_in_summer(self):
        """Europe/Berlin in July shows (CEST)."""
        self.editor.date_var.set('2026-07-01')
        self.editor.hour_var.set('12')
        self.editor.min_var.set('00')
        self.editor.tz_var.set('Europe/Berlin')
        self.root.update_idletasks()
        self.assertEqual(self.editor.tz_abbr_var.get(), '(CEST)')

    def test_tz_abbr_cet_in_winter(self):
        """Europe/Berlin in January shows (CET)."""
        self.editor.date_var.set('2026-01-15')
        self.editor.hour_var.set('12')
        self.editor.min_var.set('00')
        self.editor.tz_var.set('Europe/Berlin')
        self.root.update_idletasks()
        self.assertEqual(self.editor.tz_abbr_var.get(), '(CET)')

    def test_tz_abbr_empty_without_tz(self):
        """No timezone set means no abbreviation, and the (UTC) warning appears."""
        self.editor.date_var.set('2026-07-01')
        self.editor.hour_var.set('12')
        self.editor.min_var.set('00')
        self.root.update_idletasks()
        # Abbreviation label should show (UTC) blinking warning
        self.assertEqual(self.editor.tz_abbr_var.get(), '(UTC)')

    def test_tz_abbr_invalid_tz(self):
        """An invalid timezone like 'Europe/' shows (UTC) warning."""
        self.editor.date_var.set('2026-07-01')
        self.editor.hour_var.set('12')
        self.editor.min_var.set('00')
        self.editor.tz_var.set('Europe/')
        self.root.update_idletasks()
        self.assertEqual(self.editor.tz_abbr_var.get(), '(UTC)')


if __name__ == '__main__':
    unittest.main()
