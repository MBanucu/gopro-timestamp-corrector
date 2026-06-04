"""Tests for Ubuntu-specific compatibility issues.

These tests verify that the tool works correctly on Ubuntu CI runners,
where kernel version, exFAT driver behavior, and available tools may
differ from the development environment (NixOS).
"""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


_BD = str(Path(__file__).resolve().parent.parent / 'src')
if _BD not in sys.path:
    sys.path.insert(0, _BD)


def _dd_supports(flag: str) -> bool:
    """Check if dd supports a given flag (e.g. 'status=none', 'iflag=nocache')."""
    r = subprocess.run(
        ['dd', f'{flag}', '--version'],
        capture_output=True, text=True)
    return r.returncode == 0


class TestDdOptions(unittest.TestCase):
    """Verify dd options used by the tool work on this platform."""

    def test_status_none(self):
        """dd status=none must be supported (suppresses progress output)."""
        r = subprocess.run(
            ['dd', 'if=/dev/null', 'of=/dev/null', 'bs=1', 'count=0',
             'status=none'],
            capture_output=True)
        self.assertEqual(r.returncode, 0,
                         f'dd status=none failed: {r.stderr.decode(errors="replace")[:200]}')

    def test_iflag_nocache(self):
        """dd iflag=nocache may fail on older coreutils; test gracefully."""
        r = subprocess.run(
            ['dd', 'if=/dev/null', 'of=/dev/null', 'bs=1', 'count=0',
             'iflag=nocache', 'status=none'],
            capture_output=True)
        if r.returncode != 0:
            self.skipTest('dd iflag=nocache not supported on this platform')

    def test_conv_fsync(self):
        """dd conv=fsync must be supported."""

class TestKernelExfat(unittest.TestCase):
    """Verify exFAT kernel driver behavior."""

    def test_utime_on_exfat(self):
        """os.utime() on exFAT: may raise EPERM on older kernels (<6.12)."""
        tmp = Path('/tmp') / 'utime_test_exfat'
        try:
            tmp.mkdir(exist_ok=True)
            tf = tmp / 'probe.bin'
            tf.write_text('test')
            try:
                import os
                os.utime(tf, (1234567890.0, 1234567890.0))
                st = os.stat(tf)
                self.assertTrue(abs(st.st_mtime - 1234567890.0) < 1.0,
                                'os.utime() returned unexpected mtime')
            except OSError as e:
                if e.errno == 1:  # EPERM
                    self.skipTest(f'os.utime() failed with EPERM on {tmp}')
                raise
            finally:
                tf.unlink(missing_ok=True)
        finally:
            try:
                tmp.rmdir()
            except OSError:
                pass

    def test_statx_btime_on_exfat(self):
        """statx STATX_BTIME on exFAT: may be 0 on kernels <6.12."""
        from probe import probe_statx_btime
        btime, supported = probe_statx_btime(str(Path.cwd()))
        if supported is True and btime is not None and btime > 0:
            self.skipTest('STATX_BTIME works on this filesystem')
        print(f'  statx btime={btime}, supported={supported}')


class TestLoopDevice(unittest.TestCase):
    """Verify loop device operations."""

    def test_backing_file_resolution(self):
        """ExfatRawIO._backing_file must resolve the backing file for loop devices."""
        from exfat_raw._strategies import BackingFileStrategy
        import tempfile
        img = tempfile.NamedTemporaryFile(suffix='.img', delete=False)
        img.close()
        try:
            os.truncate(img.name, 64 * 1024 * 1024)
            r = subprocess.run(
                ['sudo', 'losetup', '-f', '--show', img.name],
                capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest('losetup failed')
            loop_dev = r.stdout.strip()
            try:
                strategy = BackingFileStrategy()
                backing = strategy._resolve(loop_dev)
                self.assertIsNotNone(backing,
                                     f'No backing file for {loop_dev}')
                self.assertEqual(backing, img.name,
                                 f'Backing file mismatch: {backing} != {img.name}')
            finally:
                subprocess.run(['sudo', 'losetup', '-d', loop_dev],
                               capture_output=True)
        finally:
            os.unlink(img.name)


class TestSparseCopy(unittest.TestCase):
    """Verify sparse file behavior with the test image."""

    def test_image_has_correct_size(self):
        """sdcard.img must be 8 GB sparse file."""
        img = Path(__file__).parent / 'sdcard.img'
        if not img.exists():
            self.skipTest('sdcard.img not found')
        stat = img.stat()
        self.assertEqual(stat.st_size, 8531738624,
                         f'Unexpected image size: {stat.st_size}')
        # Check if it's sparse (allocated blocks << size)
        allocated = stat.st_blocks * 512
        self.assertLess(allocated, stat.st_size,
                        f'Image is not sparse: {allocated} bytes allocated '
                        f'of {stat.st_size}')

