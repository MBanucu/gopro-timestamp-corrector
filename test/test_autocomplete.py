"""Tests for autocomplete behavior (first-match with common-prefix fallback)."""

import unittest

from shared import HAS_TK

if HAS_TK:
    import tkinter as tk
    from tkinter import ttk
    from test.shared import make_cb


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestAutocomplete(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.cb = make_cb(self.root)

    def tearDown(self):
        self.root.destroy()

    def _trigger(self):
        self.cb._do_autocomplete()
        self.root.update_idletasks()

    def _set(self, text):
        self.cb.entry.delete(0, tk.END)
        self.cb.entry.insert(0, text)
        self.cb.entry.icursor(len(text))
        self.root.update_idletasks()

    def _sel(self):
        try:
            ss = self.cb.entry.index(tk.SEL_FIRST)
            se = self.cb.entry.index(tk.SEL_LAST)
        except tk.TclError:
            ss = self.cb.entry.index(tk.INSERT)
            se = ss
        return ss, se

    def _check(self, setup, exp_text, exp_ss, exp_se):
        self._set(setup)
        self._trigger()
        text = self.cb.entry.get()
        ss, se = self._sel()
        self.assertEqual(text, exp_text, f'text mismatch for {setup!r}')
        self.assertEqual((ss, se), (exp_ss, exp_se),
                         f'selection mismatch for {setup!r}: '
                         f'got ({ss},{se}) want ({exp_ss},{exp_se})')

    def test_type_E_fills_EET(self):
        self._check('E', 'EET', 1, 3)

    def test_type_e_fills_EET(self):
        self._check('e', 'EET', 1, 3)

    def test_type_Eu_prefix(self):
        self._check('Eu', 'Europe/', 2, 7)

    def test_type_Eur_prefix(self):
        self._check('Eur', 'Europe/', 3, 7)

    def test_type_Europe_B_full(self):
        self._check('Europe/B', 'Europe/Berlin', 8, 13)

    def test_exact_match_stays(self):
        self._check('EST', 'EST', 3, 3)

    def test_no_match_stays(self):
        self._check('Xy', 'Xy', 2, 2)

    # --- Tab acceptance ---

    def test_tab_clears_selection(self):
        self._set('Eur')
        self._trigger()
        self.cb._on_tab(None)
        text = self.cb.entry.get()
        ss, se = self._sel()
        self.assertEqual(text, 'Europe/')
        self.assertEqual(ss, se)

    def test_tab_without_selection_does_not_break(self):
        self._set('Europe/Berlin')
        self.cb.entry.icursor(tk.END)
        self.root.update_idletasks()
        result = self.cb._on_tab(None)
        self.assertIsNone(result)

    # --- Shift modifier ---

    def test_shift_release_does_not_narrow(self):
        self._set('E')
        self._trigger()
        self.assertEqual(len(self.cb._filtered), 10)
        self.cb.entry.event_generate('<KeyRelease>', keysym='Shift_L')
        self.root.update_idletasks()
        self.assertEqual(len(self.cb._filtered), 10)

    # --- Focus in / tab navigation ---

    def test_focus_in_selects_all_and_resets_filter(self):
        self._set('Europe/Berlin')
        self._trigger()
        self.assertEqual(len(self.cb._filtered), 1)
        # Simulate tabbing away and back
        self.cb.entry.event_generate('<FocusIn>')
        self.root.update_idletasks()
        self.assertEqual(len(self.cb._filtered), 10,
                         'FocusIn should reset filter to all entries')
        # Verify text is selected
        try:
            ss = self.cb.entry.index(tk.SEL_FIRST)
            se = self.cb.entry.index(tk.SEL_LAST)
            self.assertEqual((ss, se), (0, 13),
                             f'FocusIn should select all text, got ({ss},{se})')
        except tk.TclError:
            self.fail('FocusIn should select all text')

    def test_tab_away_and_back_typing_E_returns_10_proposals(self):
        """Typing 'E' after tabbing away and back should show 10 proposals."""
        self._set('Europe/Berlin')
        self._trigger()
        # Tab away (FocusOut) and back (FocusIn)
        self.cb.entry.event_generate('<FocusOut>')
        self.root.update_idletasks()
        self.cb.entry.event_generate('<FocusIn>')
        self.root.update_idletasks()
        # Type 'E' — should replace the selected text
        self.cb.entry.delete(0, tk.END)
        self.cb.entry.insert(0, 'E')
        self.cb.entry.icursor(1)
        self.root.update_idletasks()
        self.cb._do_autocomplete()
        self.root.update_idletasks()
        self.assertEqual(len(self.cb._filtered), 10,
                         'Typing E after tab back should show 10 proposals')

    def test_rapid_typing_Eur_highlights_suffix(self):
        """Simulate typing 'Eur' quickly and verify the suffix is highlighted."""
        self.cb.entry.delete(0, tk.END)
        self.root.update_idletasks()

        # Type 'E' → triggers autocomplete → "EET" with "ET" selected
        self.cb.entry.insert(0, 'E')
        self.cb.entry.icursor(1)
        self.cb._do_autocomplete()
        self.assertEqual(self.cb.entry.get(), 'EET',
                         'After E: should show EET')

        # Type 'u' → replaces selected "ET" → "Eu"
        self.cb.entry.delete(1, 3)
        self.cb.entry.insert(1, 'u')
        self.cb.entry.icursor(2)
        self.cb._do_autocomplete()
        self.assertEqual(self.cb.entry.get(), 'Europe/',
                         'After u: should show Europe/ (common prefix for Eu)')

        # Type 'r' → replaces selected "ope/" → "Eur"
        self.cb.entry.delete(2, 7)
        self.cb.entry.insert(2, 'r')
        self.cb.entry.icursor(3)
        self.cb._do_autocomplete()
        self.root.update_idletasks()

        text = self.cb.entry.get()
        self.assertEqual(text, 'Europe/',
                         f'After Eur: should show Europe/, got {repr(text)}')

        try:
            ss = self.cb.entry.index(tk.SEL_FIRST)
            se = self.cb.entry.index(tk.SEL_LAST)
        except tk.TclError:
            ss = self.cb.entry.index(tk.INSERT)
            se = ss
            self.fail(f'Suffix should be selected, but nothing selected (cursor at {ss})')

        self.assertEqual((ss, se), (3, 7),
                         f'Suffix "ope/" at positions 3-7 should be selected, got ({ss},{se})')

    def test_stale_key_release_does_not_overwrite(self):
        """KeyRelease for 'u' after 'r' was released must not re-autocomplete."""
        # This reproduces: press e, release e, press u, press r, release r, release u
        # The 'u' KeyRelease arrives after 'r' autocomplete has set text to "Europe/"
        self.cb.entry.delete(0, tk.END)
        self.root.update_idletasks()

        # Step 1: press e, release e → autocomplete → "EET"
        self.cb.entry.insert(0, 'e')
        self.cb.entry.icursor(1)
        self.cb._do_autocomplete()
        self.assertEqual(self.cb.entry.get(), 'EET')

        # Step 2: press u (KeyPress replaces "ET" with "u"), but DON'T release yet
        self.cb.entry.delete(1, 3)
        self.cb.entry.insert(1, 'u')
        self.cb.entry.icursor(2)

        # Step 3: press r (KeyPress inserts "r"), then release r
        self.cb.entry.insert(2, 'r')
        self.cb.entry.icursor(3)
        self.cb._do_autocomplete()   # release 'r'

        # After releasing 'r', text should be "Europe/"
        self.assertEqual(self.cb.entry.get(), 'Europe/')

        # Step 4: release u — this is a stale KeyRelease
        # When _on_key processes keysym='u', the entry text is "Europe/",
        # which does NOT end with 'u'. So _do_autocomplete is skipped.
        self.cb._on_key(self._make_key_event('u'))
        self.root.update_idletasks()

        self.assertEqual(self.cb.entry.get(), 'Europe/',
                         'Stale u-key release should NOT change text to Europe/Amsterdam')

    def _make_key_event(self, keysym):
        """Create a lightweight mock event for _on_key."""
        class MockEvent:
            pass
        ev = MockEvent()
        ev.keysym = keysym
        return ev


if __name__ == '__main__':
    unittest.main()
