"""Tests for _common_prefix()."""

import unittest
import tkinter as tk

from gui.tzcombobox import FilteringCombobox
from test.shared import TEST_ZONES


class TestCommonPrefix(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.cb = FilteringCombobox(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_eur_prefix(self):
        matches = [z for z in TEST_ZONES if z.lower().startswith('eur')]
        self.assertEqual(self.cb._common_prefix(matches, 'Eur'), 'Europe/')

    def test_europe_be_full_match(self):
        matches = [z for z in TEST_ZONES if z.lower().startswith('europe/be')]
        self.assertEqual(self.cb._common_prefix(matches, 'Europe/Be'), 'Europe/Berlin')

    def test_no_matches(self):
        self.assertEqual(self.cb._common_prefix([], 'Xyz'), 'Xyz')

    def test_e_returns_typed(self):
        matches = [z for z in TEST_ZONES if z.lower().startswith('e')]
        self.assertEqual(self.cb._common_prefix(matches, 'E'), 'E')


if __name__ == '__main__':
    unittest.main()
