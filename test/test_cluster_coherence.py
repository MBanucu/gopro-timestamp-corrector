"""Diagnostic test: verify raw block writes don't corrupt unrelated directory clusters.

Isolates the test_07 issue where writing a file entry corrupts the parent
directory's cluster, making subsequent lookups fail.
"""

import os
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared import decompress_sparse_image, setup_loop_device, teardown_loop_device


_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)


class TestClusterCoherence(unittest.TestCase):
    """Verify that writing a file entry via raw block doesn't affect other clusters."""

    @classmethod
    def setUpClass(cls):
        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)
        cls._work_dir = Path(tempfile.mkdtemp(prefix='gopro_coh_'))
        cls._img_path = cls._work_dir / 'sdcard.img'
        subprocess.run(['cp', '--sparse=always', str(cached), str(cls._img_path)],
                       check=True, capture_output=True)
        cls._loop_dev, cls._mount_point = setup_loop_device(cls._img_path)
        cls._target = Path(cls._mount_point) / 'DCIM' / '100GOPRO'
        if not cls._target.exists():
            teardown_loop_device(cls._loop_dev, cls._mount_point)
            raise unittest.SkipTest(f'{cls._target} not found')

    @classmethod
    def tearDownClass(cls):
        teardown_loop_device(cls._loop_dev, cls._mount_point)
        if cls._work_dir:
            import shutil
            shutil.rmtree(cls._work_dir, ignore_errors=True)

    def _device(self):
        from btime import _resolve_device
        return _resolve_device(str(self._mount_point))

    def _boot(self):
        from strategies.exfat_raw import _exfat_parse_boot
        return _exfat_parse_boot(self._device())

    def _read_raw(self, device, offset, size):
        """Read raw bytes from the backing file (not loop device) to avoid cache."""
        backing = self._backing_file(device)
        target = backing or device
        r = subprocess.run(
            ['sudo', 'dd', f'if={target}', 'bs=1', f'skip={offset}',
             f'count={size}', 'status=none'],
            capture_output=True)
        return r.stdout

    def _backing_file(self, device):
        try:
            r = subprocess.run(
                ['sudo', 'losetup', '-l', device, '--noheadings', '-O', 'BACK-FILE'],
                capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
        except Exception:
            return None

    def _cluster_offset(self, boot, cluster):
        heap_off = boot['cluster_heap_offset']
        cs = boot['cluster_size']
        return heap_off + (cluster - 2) * cs

    def _find_dir_cluster(self, boot, device, name, start_cluster):
        """Find a directory entry and return its first data cluster."""
        from strategies.exfat_raw import _exfat_find_in_dir
        found = _exfat_find_in_dir(boot, device, start_cluster, name)
        if not found:
            return None
        chain, ci, off, sc, entries = found
        stream = entries[1]
        return struct.unpack_from('<I', stream, 0x14)[0]

    def test_write_does_not_corrupt_parent_cluster(self):
        """Modify a file entry, then verify the parent directory cluster is unchanged."""
        boot = self._boot()
        dev = self._device()
        dev_str = str(dev)
        cs = boot['cluster_size']
        back_dev = self._backing_file(dev_str) or dev_str

        # Find DCIM directory cluster
        dcim_cluster = self._find_dir_cluster(boot, dev_str, 'DCIM', boot['root_cluster'])
        self.assertIsNotNone(dcim_cluster, 'DCIM directory not found')

        # Find 100GOPRO directory cluster
        gopro_cluster = self._find_dir_cluster(boot, dev_str, '100GOPRO', dcim_cluster)
        self.assertIsNotNone(gopro_cluster, '100GOPRO directory not found')

        # Read DCIM cluster BEFORE any modification (raw from backing file)
        dcim_offset = self._cluster_offset(boot, dcim_cluster)
        dcim_before = self._read_raw(back_dev, dcim_offset, cs)

        # Read 100GOPRO cluster BEFORE modification
        gopro_offset = self._cluster_offset(boot, gopro_cluster)
        gopro_before = self._read_raw(back_dev, gopro_offset, cs)

        # Modify the first file entry in 100GOPRO
        files = sorted(self._target.glob('*'))
        self.assertGreaterEqual(len(files), 1)
        first = str(files[0])

        from strategies.exfat_raw import read_exfat_mtime_raw, _fix_exfat_raw
        import media
        orig_mtime = media.read_mtime(first)
        target_dt = orig_mtime + timedelta(hours=-2)
        _fix_exfat_raw(first, target_dt, dry_run=False)

        # Re-read DCIM cluster from backing file
        dcim_after = self._read_raw(back_dev, dcim_offset, cs)

        # Re-read 100GOPRO cluster
        gopro_after = self._read_raw(back_dev, gopro_offset, cs)

        # Verify DCIM cluster is unchanged
        self.assertEqual(dcim_before, dcim_after,
                         'DCIM directory cluster was modified by file write!')

        # Verify 100GOPRO cluster IS changed (the write should have modified it)
        self.assertNotEqual(gopro_before, gopro_after,
                            '100GOPRO cluster was not modified by file write')

        # Verify subsequent lookups still work
        from strategies.exfat_raw import _exfat_find_in_dir
        for attempt in range(3):
            found = _exfat_find_in_dir(boot, dev_str, dcim_cluster, '100GOPRO')
            self.assertIsNotNone(found, f'100GOPRO lookup failed after write (attempt {attempt})')
            if found:
                break
            subprocess.run(['sync'])
            subprocess.run(['sudo', 'sh', '-c', 'echo 3 > /proc/sys/vm/drop_caches'], capture_output=True)

        # Also verify the file itself is findable
        from strategies.exfat_raw import _exfat_find_file_entry
        re_found = _exfat_find_file_entry(boot, dev_str, first)
        self.assertIsNotNone(re_found, f'Could not find {files[0].name} after write')

        # Verify the written mtime via raw block
        raw_mtime = read_exfat_mtime_raw(first)
        expected_ts = int(target_dt.replace(tzinfo=timezone.utc).timestamp())
        self.assertIsNotNone(raw_mtime, 'Could not read mtime after write')
        self.assertEqual(raw_mtime, expected_ts,
                         f'Raw mtime ({raw_mtime}) != expected ({expected_ts})')


if __name__ == '__main__':
    unittest.main()
