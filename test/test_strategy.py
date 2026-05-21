import json
import sys
import unittest
import subprocess
import re
import tempfile
from pathlib import Path

from shared import decompress_sparse_image


class TestStrategyManifestISO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mount_point = None
        cls.loop_dev = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f"Compressed image not found at {gz_path}")
        img_path = Path(__file__).parent / 'sdcard.img'
        cls.iso_path = decompress_sparse_image(gz_path, img_path)

        try:
            res = subprocess.run(['udisksctl', 'loop-setup', '-f', str(cls.iso_path),
                                  '--no-user-interaction'],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                raise unittest.SkipTest("udisksctl loop-setup failed")
            m = re.search(r'as (/dev/loop\d+)', res.stdout)
            cls.loop_dev = m.group(1) if m else None
            if not cls.loop_dev:
                raise unittest.SkipTest("Could not parse loop device")

            res = subprocess.run(['udisksctl', 'mount', '-b', cls.loop_dev,
                                  '--no-user-interaction'],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                if 'AlreadyMounted' in res.stderr:
                    m = re.search(r'at `([^`]+)\'', res.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                if not cls.mount_point:
                    m_res = subprocess.run(['mount'], capture_output=True, text=True)
                    for line in m_res.stdout.splitlines():
                        if cls.loop_dev in line:
                            cls.mount_point = line.split()[2]
                            break
                if not cls.mount_point:
                    raise unittest.SkipTest("udisksctl mount failed")
            else:
                m = re.search(r'at ([^ \n]+)', res.stdout)
                if m:
                    cls.mount_point = m.group(1).rstrip('.')
                else:
                    raise unittest.SkipTest("Could not parse mount point")
        except FileNotFoundError:
            raise unittest.SkipTest("udisksctl not found")
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.loop_dev:
            r = subprocess.run(['udisksctl', 'unmount', '-b', cls.loop_dev,
                                '--no-user-interaction'], capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.run(['sudo', 'umount', cls.loop_dev], capture_output=True)
            subprocess.run(['sudo', 'losetup', '-d', cls.loop_dev], capture_output=True)

    def _target(self):
        p = Path(self.mount_point) / 'DCIM' / '100GOPRO'
        if not p.exists():
            self.skipTest(f"Expected directory {p} not found")
        return p

    def _run_cli(self, args):
        cmd = [sys.executable, 'src/correct_timestamps.py'] + args
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_all_gps_strategy(self):
        target = self._target()
        manifest = {
            'version': 1,
            'sets': {
                '010063': {'strategy': 'gps'},
                '010064': {'strategy': 'gps'},
                '010065': {'strategy': 'gps'},
                '010066': {'strategy': 'gps'},
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            mpath = f.name
        try:
            res = self._run_cli([str(target), '--dry-run', '--strategy-manifest', mpath])
            self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
            self.assertIn('DRY RUN', res.stdout)
            # All 12 files should show as processes (no skipped)
            self.assertIn('DRY RUN - 12 would be processed', res.stdout)
            # Each set should use per-set GPS: 063 from 2021, others from 2026
            self.assertIn('gps (embedded)', res.stdout)
        finally:
            Path(mpath).unlink(missing_ok=True)

    def test_mixed_strategies(self):
        target = self._target()
        manifest = {
            'version': 1,
            'sets': {
                '010063': {'strategy': 'gps'},
                '010064': {'strategy': 'skip'},
                '010065': {'strategy': 'manual'},
                '010066': {'strategy': 'skip'},
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            mpath = f.name
        try:
            # Need --gps for the manual set's global delta
            res = self._run_cli([str(target), '--dry-run', '--gps',
                                 '--strategy-manifest', mpath])
            self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
            # 063 = 3 files GPS, 065 = 3 files manual = 6 processed
            # 064 + 066 = 6 files skipped (target == current, counted as "already correct")
            self.assertIn('6 would be processed', res.stdout)
            self.assertIn('6 already correct', res.stdout)
            # 063 should show gps source tag
            self.assertIn('gps (embedded)', res.stdout)
            # 065 should show manual source tag
            self.assertIn('manual (embedded)', res.stdout)
        finally:
            Path(mpath).unlink(missing_ok=True)

    def test_skip_all(self):
        target = self._target()
        manifest = {
            'version': 1,
            'sets': {
                '010063': {'strategy': 'skip'},
                '010064': {'strategy': 'skip'},
                '010065': {'strategy': 'skip'},
                '010066': {'strategy': 'skip'},
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            mpath = f.name
        try:
            res = self._run_cli([str(target), '--dry-run', '--strategy-manifest', mpath])
            self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
            self.assertIn('No changes needed.', res.stdout)
            self.assertNotIn('would be processed', res.stdout)
        finally:
            Path(mpath).unlink(missing_ok=True)

    def test_partial_manifest_defaults_to_manual(self):
        target = self._target()
        manifest = {
            'version': 1,
            'sets': {
                '010063': {'strategy': 'gps'},
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            mpath = f.name
        try:
            res = self._run_cli([str(target), '--dry-run', '--gps',
                                 '--strategy-manifest', mpath])
            self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
            # 063 = 3 GPS, 064-066 = 9 manual (default) = 12 total
            self.assertIn('12 would be processed', res.stdout)
        finally:
            Path(mpath).unlink(missing_ok=True)

    def test_invalid_strategy_falls_back_to_manual(self):
        target = self._target()
        manifest = {
            'version': 1,
            'sets': {
                '010063': {'strategy': 'invalid_option'},
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            mpath = f.name
        try:
            res = self._run_cli([str(target), '--dry-run', '--gps',
                                 '--strategy-manifest', mpath])
            self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
            # Invalid strategy falls back to manual, all 12 processed with manual tag
            self.assertIn('12 would be processed', res.stdout)
            self.assertIn('manual (embedded)', res.stdout)
        finally:
            Path(mpath).unlink(missing_ok=True)

    def test_per_set_gps_delta_applied(self):
        target = self._target()
        # Set 010063 has GPS from 2021 (old), 010064 has GPS from 2026
        manifest = {
            'version': 1,
            'sets': {
                '010063': {'strategy': 'gps'},
                '010064': {'strategy': 'gps'},
            }
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(manifest, f)
            mpath = f.name
        try:
            res = self._run_cli([str(target), '--dry-run', '--strategy-manifest', mpath])
            self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
            # 063 GPS from 2021 → delta is years negative, target date should be 2021
            self.assertIn('2021-03-11', res.stdout)
            # 064 GPS from 2026 → delta is small (about -2h), target stays in 2026
            self.assertIn('2026-05-14 15:02:27', res.stdout)
        finally:
            Path(mpath).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
