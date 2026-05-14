import unittest
import subprocess
import os
import re
from pathlib import Path

class TestISOIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iso_path = Path(__file__).parent / 'sdcard.iso'
        cls.mount_point = None
        cls.loop_dev = None

        if not cls.iso_path.exists():
            raise unittest.SkipTest(f"ISO not found at {cls.iso_path}")

        try:
            # Try to set up loop device
            res = subprocess.run(['udisksctl', 'loop-setup', '-f', str(cls.iso_path), '--no-user-interaction'],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                raise unittest.SkipTest("udisksctl loop-setup failed (permissions?)")
            
            m = re.search(r'as (/dev/loop\d+)', res.stdout)
            if not m:
                raise unittest.SkipTest("Could not parse loop device path")
            cls.loop_dev = m.group(1)

            # Try to mount
            res = subprocess.run(['udisksctl', 'mount', '-b', cls.loop_dev, '--no-user-interaction'],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                # Check if already mounted
                if 'AlreadyMounted' in res.stderr:
                    m = re.search(r'at `([^`]+)\'', res.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                    else:
                        # Fallback: check mount command
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

    @classmethod
    def tearDownClass(cls):
        if cls.loop_dev:
            # We don't always unmount/delete because udisksctl can be picky in non-interactive shells
            # but we try.
            if cls.mount_point:
                subprocess.run(['udisksctl', 'unmount', '-b', cls.loop_dev, '--no-user-interaction'],
                               capture_output=True)
            subprocess.run(['udisksctl', 'loop-delete', '-b', cls.loop_dev, '--no-user-interaction'],
                           capture_output=True)

    def test_gps_correction_on_iso(self):
        target = Path(self.mount_point) / 'DCIM' / '100GOPRO'
        if not target.exists():
            self.skipTest(f"Expected directory {target} not found on ISO")

        cmd = ['python3', 'correct_timestamps.py', str(target), '--gps', '--dry-run']
        res = subprocess.run(cmd, capture_output=True, text=True)
        
        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
        self.assertIn("Using GPS from GL010061.LRV", res.stdout)
        self.assertIn("DRY RUN - 3 would be processed", res.stdout)
        self.assertIn("GX010061.MP4", res.stdout)
        self.assertIn("2026-05-14 12:13:11.499000", res.stdout)

if __name__ == '__main__':
    unittest.main()
