"""GUI integration test: run full correction via ToolGUI, verify btime was set correctly.

Mounts a copy of the sdcard image, opens the full GUI, applies a known
calibration delta with exfat_raw btime, and checks that every file's
birth time matches the expected target (original mtime + delta).
"""
import os
import shutil
import subprocess
import re
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path

from shared import HAS_TK, decompress_sparse_image


@unittest.skipUnless(HAS_TK, 'Tkinter not available')
class TestBtimeGuiCorrection(unittest.TestCase):
    """Full GUI correction on a copy of sdcard.img, verifying btime is set correctly."""

    @classmethod
    def setUpClass(cls):
        cls._work_dir = None
        cls.mount_point = None
        cls.loop_dev = None
        cls.target = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')

        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_btime_test_'))
        cls.img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls.img_path)],
                       check=True, capture_output=True)

        try:
            r = subprocess.run(
                ['udisksctl', 'loop-setup', '-f', str(cls.img_path),
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise unittest.SkipTest('udisksctl loop-setup failed')
            m = re.search(r'as (/dev/loop\d+)', r.stdout)
            cls.loop_dev = m.group(1) if m else None
            if not cls.loop_dev:
                raise unittest.SkipTest('Could not parse loop device')

            r = subprocess.run(
                ['udisksctl', 'mount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                if 'AlreadyMounted' in r.stderr:
                    m = re.search(r"at `([^`]+)'", r.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                if not cls.mount_point:
                    raise unittest.SkipTest('udisksctl mount failed')
            else:
                m = re.search(r'at ([^ \n]+)', r.stdout)
                if m:
                    cls.mount_point = m.group(1).rstrip('.')
                else:
                    raise unittest.SkipTest('Could not parse mount point')
        except FileNotFoundError:
            raise unittest.SkipTest('udisksctl not found')

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'{cls.target} not found')

    @classmethod
    def tearDownClass(cls):
        if cls.loop_dev:
            r = subprocess.run(
                ['udisksctl', 'unmount', '-b', cls.loop_dev,
                 '--no-user-interaction'],
                capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(['sudo', 'umount', cls.loop_dev],
                               capture_output=True)
            subprocess.run(['sudo', 'losetup', '-d', cls.loop_dev],
                           capture_output=True)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    @staticmethod
    def _read_btime(path):
        r = subprocess.run(['stat', '-c', '%W', str(path)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            return int(r.stdout.strip())
        except ValueError:
            return None

    def test_btime_corrected_via_gui_full_flow(self):
        """Run the full GUI correction and verify btime was set to the correct target."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

        import tkinter as tk
        from gui.app import ToolGUI
        import analysis as an_mod
        import media

        root = tk.Tk()
        root.update_idletasks()
        gui = ToolGUI(root)
        root.update_idletasks()

        # ── Set directory ──────────────────────────────────────────
        gui.step1.dir_var.set(str(self.target))
        root.update_idletasks()

        # ── Run analysis directly (not via GUI thread) ─────────────
        result = an_mod.analyze(self.target)
        self.assertIsNotNone(result)
        gui.step2.load_analysis(result)
        gui._step_completed[1] = True
        root.update_idletasks()

        # ── Record original metadata for every file ─────────────────
        orig_mtimes = {}
        media_files = media.collect(self.target)
        for fp in media_files:
            ts = media.read_mtime(fp)
            if ts is not None:
                orig_mtimes[fp] = int(ts.timestamp())

        # ── Set a known calibration delta ──────────────────────────
        delta = timedelta(hours=-2)
        gui.step2.manual_delta = delta
        root.update_idletasks()

        # ── Advance to plan step, enable btime with full chain ─────
        gui._advance_to_plan()
        root.update_idletasks()

        gui.step3.fix_btime_var.set(True)
        gui.step3._toggle_btime()
        gui.step3.dry_run_var.set(False)
        gui.step3._btime_methods = ['exfat_raw']
        gui.step3._rebuild_listbox()
        root.update_idletasks()

        # ── Advance to run step and trigger correction ─────────────
        gui._advance_to_run()
        root.update_idletasks()

        # ── Run correction (run_tool starts a thread) ──────────────
        done = threading.Event()
        orig_on_finish = gui.on_finish
        def on_finish(code):
            gui._exit_code = code
            try:
                orig_on_finish(code)
            finally:
                done.set()
                root.quit()
        gui.on_finish = on_finish

        gui.run_tool()
        root.after(60000, root.quit)
        root.mainloop()

        self.assertTrue(done.is_set(), 'Correction did not complete within 60s')
        self.assertEqual(getattr(gui, '_exit_code', -1), 0,
                         'Correction failed')
        root.update_idletasks()

        # ── Verify btime on every file ─────────────────────────────
        errors = []
        for fp in media_files:
            after_bt = self._read_btime(fp)
            if after_bt is None:
                errors.append(f'{fp.name}: could not read btime')
                continue

            if fp not in orig_mtimes:
                errors.append(f'{fp.name}: no original mtime recorded')
                continue
            expected_ts = orig_mtimes[fp] + int(delta.total_seconds())

            diff = abs(after_bt - expected_ts)
            if diff > 2:
                errors.append(
                    f'{fp.name}: btime {after_bt}, '
                    f'expected ~{expected_ts} (diff={diff}s)')

        root.destroy()
        self.assertEqual(errors, [], '\n'.join(errors))
