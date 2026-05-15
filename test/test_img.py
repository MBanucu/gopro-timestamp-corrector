import unittest
import subprocess
import re
from pathlib import Path

from shared import decompress_sparse_image


class TestImgIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mount_point = None
        cls.loop_dev = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f"Compressed image not found at {gz_path}")
        img_path = Path(__file__).parent / 'sdcard.img'
        cls.img_path = decompress_sparse_image(gz_path, img_path)

        try:
            res = subprocess.run(['udisksctl', 'loop-setup', '-f', str(cls.img_path),
                                  '--no-user-interaction'],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                raise unittest.SkipTest("udisksctl loop-setup failed (permissions?)")

            m = re.search(r'as (/dev/loop\d+)', res.stdout)
            cls.loop_dev = m.group(1) if m else None
            if not cls.loop_dev:
                raise unittest.SkipTest("Could not parse loop device path")

            res = subprocess.run(['udisksctl', 'mount', '-b', cls.loop_dev,
                                  '--no-user-interaction'],
                                 capture_output=True, text=True)
            if res.returncode != 0:
                if 'AlreadyMounted' in res.stderr:
                    m = re.search(r'at `([^`]+)\'', res.stderr)
                    if m:
                        cls.mount_point = m.group(1)
                    else:
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

    def test_gps_correction_on_img(self):
        target = Path(self.mount_point) / 'DCIM' / '100GOPRO'
        if not target.exists():
            self.skipTest(f"Expected directory {target} not found on image")

        cmd = ['python3', 'src/correct_timestamps.py', str(target), '--gps', '--dry-run']
        res = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
        self.assertIn("Using GPS from GL010063.LRV", res.stdout)
        self.assertIn("DRY RUN - 12 would be processed", res.stdout)
        self.assertIn("GX010063.MP4", res.stdout)
        self.assertIn("2021-03-11 12:51:00.199000", res.stdout)


if __name__ == '__main__':
    unittest.main()
