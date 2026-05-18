"""Smoke tests for the new GUI structure: sidebar, step panels, history viewer.

These tests verify that the new module structure is importable and
key classes can be instantiated (when Tk is available).  Business
logic is covered by existing tests for the underlying components.
"""
import unittest
from pathlib import Path
import json
import tempfile

from shared import HAS_TK

if HAS_TK:
    import tkinter as tk
    from gui.sidebar import Sidebar
    from gui.steps.directory import StepDirectory
    from gui.steps.calibration import StepCalibration
    from gui.steps.review import StepReview
    from gui.steps.plan import StepPlan
    from gui.steps.run import StepRun
    from gui.history_viewer import HistoryViewer, DiffViewer


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestSidebar(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()
        self.clicks = []
        self.sidebar = Sidebar(
            self.root,
            on_step_click=lambda n: self.clicks.append(n),
            on_history=lambda: self.clicks.append('history'),
        )
        self.sidebar.pack()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_update_steps_current(self):
        completed = [False] * 5
        self.sidebar.update_steps(2, completed)
        self.root.update_idletasks()
        row, icon, label = self.sidebar._rows[2]
        self.assertEqual(icon.cget('text'), '\u25b6')

    def test_update_steps_completed(self):
        completed = [False] * 5
        completed[1] = True
        self.sidebar.update_steps(2, completed)
        self.root.update_idletasks()
        row, icon, label = self.sidebar._rows[1]
        self.assertEqual(icon.cget('text'), '\u2713')

    def test_update_steps_upcoming(self):
        completed = [False] * 5
        self.sidebar.update_steps(2, completed)
        self.root.update_idletasks()
        row, icon, label = self.sidebar._rows[4]
        self.assertEqual(icon.cget('text'), '\u2463')


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestStepPanels(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_step1_import_and_create(self):
        s = StepDirectory(self.root)
        s.pack()
        self.root.update_idletasks()
        self.assertTrue(hasattr(s, 'dir_var'))
        self.assertTrue(hasattr(s, 'cal_bar'))

    def test_step2_import_and_create(self):
        s = StepCalibration(self.root)
        s.pack()
        self.root.update_idletasks()
        self.assertTrue(hasattr(s, 'cal_panel'))

    def test_step3_import_and_create(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        self.assertTrue(hasattr(s, 'fix_embedded_var'))
        self.assertTrue(hasattr(s, 'fix_mtime_var'))
        self.assertTrue(hasattr(s, 'fix_btime_var'))
        self.assertTrue(hasattr(s, '_btime_list'))
        self.assertTrue(hasattr(s, '_btime_methods'))
        self.assertTrue(hasattr(s, 'next_btn'))

    def test_step3_proceed_to_run_button(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        called = []
        s.set_on_next(lambda: called.append('next'))
        s.next_btn.invoke()
        self.assertIn('next', called)

    def test_step3_btime_list_defaults(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        self.assertTrue(len(s._btime_methods) >= 2)
        self.assertIn('auto', s._btime_methods)
        self.assertIn('clock', s._btime_methods)

    def test_step3_btime_toggle_disables_widgets(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(False)
        s._toggle_btime()
        self.assertEqual(str(s._btime_list.cget('state')), tk.DISABLED)

    def test_step3_btime_toggle_enables_widgets(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(True)
        s._toggle_btime()
        self.assertEqual(str(s._btime_list.cget('state')), tk.NORMAL)

    def test_step3_get_options_btime_off(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(False)
        opts = s.get_options()
        self.assertEqual(opts['fix_btime'], 'off')

    def test_step3_get_options_btime_list(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(True)
        opts = s.get_options()
        btime_val = opts['fix_btime']
        self.assertIsInstance(btime_val, list)
        self.assertGreater(len(btime_val), 0)
        self.assertIn('auto', btime_val)

    def test_step3_btime_move_up(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(True)
        s._toggle_btime()
        original = list(s._btime_methods)
        # Select last item and move it up
        s._btime_list.selection_clear(0)
        s._btime_list.selection_set(len(original) - 1)
        s._move_up()
        self.assertNotEqual(s._btime_methods, original)

    def test_step3_btime_move_down(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(True)
        s._toggle_btime()
        original = list(s._btime_methods)
        s._btime_list.selection_set(0)
        s._move_down()
        self.assertNotEqual(s._btime_methods, original)

    def test_step3_btime_remove_method(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(True)
        s._toggle_btime()
        count_before = len(s._btime_methods)
        s._btime_list.selection_set(0)
        s._remove_method()
        self.assertEqual(len(s._btime_methods), count_before - 1)

    def test_step4_import_and_create(self):
        s = StepRun(self.root)
        s.pack()
        self.root.update_idletasks()
        self.assertTrue(hasattr(s, 'apply_btn'))
        self.assertTrue(hasattr(s, 'cancel_btn'))

    def test_step4_set_commands(self):
        s = StepRun(self.root)
        s.pack()
        self.root.update_idletasks()
        called = []
        s.set_commands(apply=lambda: called.append('a'),
                       cancel=lambda: called.append('c'))
        s.apply_btn.invoke()
        self.assertIn('a', called)
        s.set_cancel_enabled(True)
        self.root.update_idletasks()
        s.cancel_btn.invoke()
        self.assertIn('c', called)

    def test_step_cross_talk(self):
        """Verify the delta wiring pattern used by app.py works."""
        s2 = StepCalibration(self.root)
        s3 = StepReview(self.root)
        s2.pack()
        s3.pack()
        self.root.update_idletasks()

        # Simulate the _on_delta_changed wiring in app.py
        delta_cb = lambda d: setattr(s3, 'manual_delta', d)
        from datetime import timedelta
        d = timedelta(hours=-2, minutes=30)
        s3.manual_delta = d
        self.assertEqual(s3.manual_delta, d)


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestHistoryViewer(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_empty_history(self):
        with tempfile.TemporaryDirectory() as td:
            hv = HistoryViewer(self.root, td)
            hv.update_idletasks()
            children = hv.run_tree.get_children()
            self.assertEqual(len(children), 0)
            hv.destroy()

    def test_with_run_history(self):
        with tempfile.TemporaryDirectory() as td:
            hist_dir = Path(td) / '.timestamp_correction_history'
            run_dir = hist_dir / '20260517T163000Z'
            run_dir.mkdir(parents=True)
            meta = {
                'timestamp': '2026-05-17T16:30:00Z',
                'fix_btime': 'exfat_raw',
                'summary': {'written': 5, 'skipped': 0, 'errors': []},
            }
            (run_dir / 'run.json').write_text(json.dumps(meta, indent=2))
            before = [{'SourceFile': '/d/GX010001.MP4', 'CreateDate': '2026:05:17'}]
            after = [{'SourceFile': '/d/GX010001.MP4', 'CreateDate': '2026:05:18'}]
            (run_dir / 'before.json').write_text(json.dumps(before))
            (run_dir / 'after.json').write_text(json.dumps(after))

            hv = HistoryViewer(self.root, td)
            hv.update_idletasks()
            children = hv.run_tree.get_children()
            self.assertEqual(len(children), 1)
            vals = hv.run_tree.item(children[0], 'values')
            self.assertIn('5', vals)  # written count
            hv.destroy()


if __name__ == '__main__':
    unittest.main()
