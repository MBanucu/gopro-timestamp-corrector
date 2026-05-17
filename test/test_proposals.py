"""Tests for the proposal (filter) list."""

import unittest

from shared import HAS_TK

if HAS_TK:
    import tkinter as tk
    from test.shared import make_cb


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestProposals(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.cb = make_cb(self.root)

    def tearDown(self):
        self.root.destroy()

    def _filter(self, text):
        self.cb.entry.delete(0, tk.END)
        self.cb.entry.insert(0, text)
        self.cb.entry.icursor(len(text))
        self.root.update_idletasks()
        self.cb._do_autocomplete()
        self.root.update_idletasks()
        return len(self.cb._filtered)

    def test_E_returns_10(self):
        self.assertEqual(self._filter('E'), 10)

    def test_e_returns_10(self):
        self.assertEqual(self._filter('e'), 10)

    def test_Eu_returns_7(self):
        self.assertEqual(self._filter('Eu'), 7)

    def test_Eur_returns_7(self):
        self.assertEqual(self._filter('Eur'), 7)

    def test_slash_returns_7(self):
        self.assertEqual(self._filter('Europe/'), 7)

    def test_Europe_B_returns_1(self):
        self.assertEqual(self._filter('Europe/B'), 1)

    def test_EST_returns_1(self):
        self.assertEqual(self._filter('EST'), 1)

    def test_none_returns_all(self):
        self.assertEqual(self._filter(''), 10)

    def test_no_match_returns_0(self):
        self.assertEqual(self._filter('Xyz'), 0)


if __name__ == '__main__':
    unittest.main()
