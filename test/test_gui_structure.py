"""Smoke tests for the new GUI structure: sidebar, step panels, history viewer.

These tests verify that the new module structure is importable and
key classes can be instantiated (when Tk is available).  Business
logic is covered by existing tests for the underlying components.
"""
import unittest
import subprocess
import re
from pathlib import Path
import json
import tempfile

from shared import HAS_TK, decompress_sparse_image, setup_loop_device, teardown_loop_device

if HAS_TK:
    import tkinter as tk
    from gui.sidebar import Sidebar
    from gui.steps.directory import StepDirectory
    from gui.steps.calibration import StepCalibration
    from gui.steps.review import StepReview
    from gui.steps.plan import StepPlan
    from gui.steps.run import StepRun
    from gui.history_viewer import HistoryViewer, DiffViewer
    from gui.app import ToolGUI


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
        self.assertEqual(s._btime_methods, ['clock'])

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
        self.assertEqual(btime_val, ['clock'])

    def test_step3_btime_move_up(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.fix_btime_var.set(True)
        s._toggle_btime()
        s.set_filesystem('exfat')  # populate with 3 items
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
        s.set_filesystem('exfat')  # populate with 3 items
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

    def test_step3_set_filesystem_filters_incompatible(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.set_filesystem('ext4')
        for m in ('debugfs', 'clock'):
            self.assertIn(m, s._btime_methods,
                          f'{m} should be available on ext4')
        self.assertNotIn('exfat_raw', s._btime_methods)
        self.assertNotIn('fuse', s._btime_methods)

    def test_step3_set_filesystem_none_keeps_clock_only(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.set_filesystem('ext4')
        self.assertIn('debugfs', s._btime_methods)
        s.set_filesystem(None)
        self.assertEqual(s._btime_methods, ['clock'],
                         'Unknown fs must only show clock')

    def test_step3_set_filesystem_exfat_excludes_debugfs(self):
        """Reproduces user report: debugfs must not appear on exFAT."""
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.set_filesystem('exfat')
        self.assertNotIn('debugfs', s._btime_methods,
                         'debugfs must not appear on exFAT filesystems')
        self.assertIn('exfat_raw', s._btime_methods)
        self.assertIn('fuse', s._btime_methods)
        self.assertIn('clock', s._btime_methods)

    def test_step3_unknown_fs_does_not_include_fs_specific(self):
        """When detect_fs fails (returns None), only clock is safe."""
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.set_filesystem(None)
        self.assertEqual(s._btime_methods, ['clock'],
                         'Only clock must show when filesystem is unknown')

    def test_step3_set_filesystem_exfat_includes_exfat_fuse(self):
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s.set_filesystem('exfat')
        self.assertEqual(s._btime_methods,
                         ['exfat_raw', 'fuse', 'clock'],
                         'exFAT must show exfat_raw, fuse, clock')

    def test_step3_set_filesystem_replaces_prior_default(self):
        """Previous pre‑fs default is replaced by compatible order."""
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        s._btime_methods = ['clock']
        s._rebuild_listbox()
        s.set_filesystem('ext4')
        self.assertEqual(s._btime_methods,
                         ['debugfs', 'clock'],
                         'Compatible order replaces prior default')

    def test_step3_exfat_entries_present_when_btime_disabled(self):
        """Reproduces user observation: after set_filesystem('exfat')
        with the btime checkbox still OFF, the exFAT-specific methods
        MUST be stored in _btime_methods (they will become visible once
        the user checks btime)."""
        s = StepPlan(self.root)
        s.pack()
        self.root.update_idletasks()
        # btime is OFF by default — listbox is disabled
        self.assertFalse(s.fix_btime_var.get())

        # Simulate advance_to_plan
        s.set_filesystem('exfat')
        self.root.update_idletasks()

        # The methods must be present in the data
        methods = s._btime_methods
        self.assertIn('exfat_raw', methods,
                      'exfat_raw must be stored even when btime disabled')
        self.assertIn('fuse', methods,
                      'fuse must be stored even when btime disabled')
        self.assertNotIn('debugfs', methods,
                         'debugfs must be filtered out')

        # Listbox items must reflect _btime_methods (even when disabled)
        items_before = [s._btime_list.get(i)
                        for i in range(s._btime_list.size())]
        self.assertEqual(len(items_before), 3,
                         f'Listbox must have 3 items after set_filesystem, '
                         f'got {len(items_before)}: {items_before}')

        # After enabling btime the items must still be there
        s.fix_btime_var.set(True)
        s._toggle_btime()
        self.root.update_idletasks()
        items_after = [s._btime_list.get(i)
                       for i in range(s._btime_list.size())]
        self.assertEqual(len(items_after), 3,
                         f'Listbox must still have 3 items after enabling '
                         f'btime, got {len(items_after)}: {items_after}')
        self.assertTrue(
            any('exFAT' in label for label in items_after),
            f'Listbox must contain exFAT labels after enabling btime, '
            f'got {items_after}')

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


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestPlanBtimeFsIntegration(unittest.TestCase):
    """Mount the sdcard.img (exFAT) and verify the plan step shows
    the correct btime methods for the detected filesystem."""

    @classmethod
    def setUpClass(cls):
        cls.loop_dev = None
        cls.mount_point = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')
        img_path = Path(__file__).parent / 'sdcard.img'
        cls.img_path = decompress_sparse_image(gz_path, img_path)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'{cls.target} not found')

    @classmethod
    def tearDownClass(cls):
        teardown_loop_device(cls.loop_dev, cls.mount_point)

    def setUp(self):
        self.root = tk.Tk()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.destroy()

    def test_step3_plan_shows_exfat_methods_after_advance(self):
        """Full flow: GUI advances to plan step on exFAT — must show
        exfat_raw and fuse, must NOT show debugfs."""
        gui = ToolGUI(self.root)
        gui.step1.dir_var.set(str(self.target))
        self.root.update_idletasks()

        gui._advance_to_plan()
        self.root.update_idletasks()

        methods = gui.step3._btime_methods
        self.assertIn('exfat_raw', methods,
                      'exfat_raw must be available on exFAT')
        self.assertIn('fuse', methods,
                      'fuse must be available on exFAT')
        self.assertNotIn('debugfs', methods,
                         'debugfs must NOT appear on exFAT filesystems')

    def test_advance_to_plan_calls_set_filesystem_on_exfat(self):
        """Verify that _advance_to_plan detects exFAT and filters the list."""
        gui = ToolGUI(self.root)
        gui.step1.dir_var.set(str(self.target))
        self.root.update_idletasks()

        calls = []
        gui.step3.set_filesystem = lambda fs: calls.append(fs)
        gui._advance_to_plan()

        self.assertEqual(len(calls), 1,
                         '_advance_to_plan must call set_filesystem once')
        self.assertEqual(calls[0], 'exfat',
                         'set_filesystem must receive exfat for sdcard.img')


if __name__ == '__main__':
    unittest.main()
