"""Tests for DatePicker grid alignment."""

import unittest
import tkinter as tk
from tkinter import ttk
from datetime import date

from datepicker import DatePicker


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


if __name__ == '__main__':
    unittest.main()
