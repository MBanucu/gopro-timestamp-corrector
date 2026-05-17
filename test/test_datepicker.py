"""Tests for DatePicker and DateTimePicker."""

import unittest
from datetime import date, datetime

from shared import HAS_TK

if HAS_TK:
    import tkinter as tk
    from tkinter import ttk
    from gui.datepicker import DatePicker, DateTimePicker


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestDatePicker(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.called = []
        self.picker = DatePicker(self.root, self.cb_handler)
        self.picker.withdraw()
        self.picker.year.set(2026)
        self.picker.month.set(4)
        self.picker.draw_days()
        self.root.update_idletasks()

    def cb_handler(self, d):
        self.called.append(d)

    def tearDown(self):
        self.picker.destroy()
        self.root.destroy()

    def _grid(self):
        return list(list(self.picker.day_frame.winfo_children())[0].winfo_children())

    def _rc(self, w):
        info = w.grid_info()
        return info.get('row', -1), info.get('column', -1)

    def test_seven_headers(self):
        headers = [c for c in self._grid() if self._rc(c)[0] == 0]
        self.assertEqual(len(headers), 7)

    def test_header_labels(self):
        headers = [(c, self._rc(c)) for c in self._grid() if self._rc(c)[0] == 0]
        for col, expected in enumerate(('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su')):
            with self.subTest(col=col):
                hdr = [c for c, _ in headers if _[1] == col]
                self.assertEqual(len(hdr), 1)
                self.assertEqual(hdr[0].cget('text'), expected)

    def test_thirty_day_buttons(self):
        btns = [c for c in self._grid()
                if self._rc(c)[0] >= 1 and type(c).__name__ == 'Button']
        self.assertEqual(len(btns), 30)

    def test_day1_at_row1_col2(self):
        for c in self._grid():
            r, co = self._rc(c)
            if r == 1 and co == 2 and c.cget('text') == '1':
                return
        self.fail('Day 1 not at row=1 col=2')

    def test_day30_at_row5_col3(self):
        for c in self._grid():
            r, co = self._rc(c)
            if r == 5 and co == 3 and c.cget('text') == '30':
                return
        self.fail('Day 30 not at row=5 col=3')

    def test_all_days_present(self):
        btns = [int(c.cget('text')) for c in self._grid()
                if self._rc(c)[0] >= 1 and type(c).__name__ == 'Button']
        self.assertEqual(sorted(btns), list(range(1, 31)))

    def test_callback_returns_correct_date(self):
        self.picker.pick(1)
        self.root.update_idletasks()
        self.assertEqual(self.called, [date(2026, 4, 1)])


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestDateTimePicker(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.called = []
        self.picker = DateTimePicker(self.root, self.cb_handler,
                                      all_zones=['Europe/Berlin'],
                                      initial_hour=14, initial_minute=30,
                                      initial_tz='Europe/Berlin')
        self.picker.withdraw()
        self.picker.year.set(2026)
        self.picker.month.set(4)
        self.picker.draw_days()
        self.root.update_idletasks()

    def cb_handler(self, dt, tz=''):
        self.called.append((dt, tz))

    def tearDown(self):
        self.picker.destroy()
        self.root.destroy()

    def test_inherits_date_grid(self):
        grid = list(self.picker.day_frame.winfo_children())
        self.assertTrue(len(grid) > 0)

    def test_has_hour_spinbox(self):
        self.assertTrue(hasattr(self.picker, 'hour_var'))
        self.assertIsInstance(self.picker.hour_var.get(), int)

    def test_has_minute_spinbox(self):
        self.assertTrue(hasattr(self.picker, 'min_var'))
        self.assertIsInstance(self.picker.min_var.get(), int)

    def test_has_tz_widget(self):
        self.assertTrue(hasattr(self.picker, 'tz_var'))
        self.assertTrue(hasattr(self.picker, 'tz_combo'))

    def test_initial_values(self):
        self.assertEqual(self.picker.hour_var.get(), 14)
        self.assertEqual(self.picker.min_var.get(), 30)
        self.assertEqual(self.picker.tz_var.get(), 'Europe/Berlin')

    def test_callback_returns_datetime_and_tz(self):
        self.picker.pick(1)
        self.root.update_idletasks()
        self.assertEqual(len(self.called), 1)
        dt, tz = self.called[0]
        self.assertIsInstance(dt, datetime)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 4)
        self.assertEqual(dt.day, 1)
        self.assertEqual(dt.hour, 14)
        self.assertEqual(dt.minute, 30)
        self.assertEqual(tz, 'Europe/Berlin')

    def test_set_now_updates_values(self):
        self.picker._set_now()
        self.root.update_idletasks()
        now = datetime.now()
        self.assertEqual(self.picker.hour_var.get(), now.hour)
        self.assertEqual(self.picker.min_var.get(), now.minute)
        self.assertEqual(self.picker.year.get(), now.year)
        self.assertEqual(self.picker.month.get(), now.month)


if __name__ == '__main__':
    unittest.main()
