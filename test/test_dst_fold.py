"""Tests for DST fold detection in the calibration editor."""

import unittest

from shared import HAS_TK

if HAS_TK:
    import tkinter as tk
    from tkinter import ttk
    from gui.editor import CalibrationEditor


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestDSTFold(unittest.TestCase):
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

    def _set(self, date_val, hour, minute, tz):
        self.editor.date_var.set(date_val)
        self.editor.hour_var.set(str(hour).zfill(2))
        self.editor.min_var.set(str(minute).zfill(2))
        self.editor.tz_var.set(tz)
        self.root.update()
        self.root.update_idletasks()

    def test_april_no_dst(self):
        self._set('2026-04-25', 14, 14, 'Europe/Berlin')
        self.assertFalse(self.editor.dst_warn_var.get())
        self.assertFalse(self.editor.fold_row.winfo_ismapped())

    def test_oct_fall_back(self):
        self._set('2026-10-25', 2, 30, 'Europe/Berlin')
        self.assertTrue(self.editor.dst_warn_var.get())
        self.assertTrue(self.editor.fold_row.winfo_ismapped())

    def test_same_day_different_time_no_dst(self):
        self._set('2026-10-25', 6, 0, 'Europe/Berlin')
        self.assertFalse(self.editor.dst_warn_var.get())
        self.assertFalse(self.editor.fold_row.winfo_ismapped())

    def test_march_spring_forward(self):
        self._set('2026-03-29', 2, 30, 'Europe/Berlin')
        self.assertTrue(self.editor.dst_warn_var.get())
        self.assertFalse(self.editor.fold_row.winfo_ismapped())

    def test_no_timezone_no_dst(self):
        self._set('2026-04-25', 14, 14, '')
        self.assertFalse(self.editor.dst_warn_var.get())
        self.assertFalse(self.editor.fold_row.winfo_ismapped())

    def test_utc_no_dst(self):
        self._set('2026-10-25', 2, 30, 'UTC')
        self.assertFalse(self.editor.dst_warn_var.get())
        self.assertFalse(self.editor.fold_row.winfo_ismapped())

    def test_ny_spring_forward(self):
        self._set('2026-03-08', 2, 30, 'America/New_York')
        self.assertTrue(self.editor.dst_warn_var.get())
        self.assertFalse(self.editor.fold_row.winfo_ismapped())


if __name__ == '__main__':
    unittest.main()
