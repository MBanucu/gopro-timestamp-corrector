import sys
import unittest
import subprocess
import re
from pathlib import Path

from shared import prepare_sparse_image, setup_loop_device, teardown_loop_device
import shutil


class TestImgIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mount_point = None
        cls.loop_dev = None
        cls._work_dir = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f"Compressed image not found at {gz_path}")
        cls._work_dir, cls.img_path = prepare_sparse_image(gz_path)

        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)

    @classmethod
    def tearDownClass(cls):
        teardown_loop_device(cls.loop_dev, cls.mount_point)
        if cls._work_dir:
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    def test_gps_correction_on_img(self):
        target = Path(self.mount_point) / 'DCIM' / '100GOPRO'
        if not target.exists():
            self.skipTest(f"Expected directory {target} not found on image")

        cmd = [sys.executable, 'src/correct_timestamps.py', str(target), '--gps', '--dry-run']
        res = subprocess.run(cmd, capture_output=True, text=True)

        self.assertEqual(res.returncode, 0, f"Script failed: {res.stderr}")
        self.assertIn("Using GPS from GL010063.LRV", res.stdout)
        self.assertIn("DRY RUN - 12 would be processed", res.stdout)
        self.assertIn("GX010063.MP4", res.stdout)
        self.assertIn("2021-03-11 12:51:00.199000", res.stdout)


if __name__ == '__main__':
    unittest.main()
