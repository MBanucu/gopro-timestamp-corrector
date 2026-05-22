"""Verify two full pipelines run in parallel do not interfere.

Hypothesis: two parallel test_full_auto_integration pipelines,
each with independent image copy and loop device, should not
affect each other's raw mtime correction results.

Expected result: both pipelines succeed (raw mtime is corrected).
If this test FAILS, there is kernel-level interference between
two independent exFAT mounts that causes writeback of dirty
inodes from one mount to affect the other.
"""
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)


class TestParallelPipelines(unittest.TestCase):
    """Run two test_full_auto_integration pipelines in parallel subprocesses."""

    def test_two_parallel_pipelines(self):
        import tempfile
        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device

        gz = Path(__file__).parent.parent / 'sdcard.img.gz'
        if not gz.exists():
            self.skipTest('sdcard.img.gz not found')
        cached = Path(__file__).parent.parent / 'sdcard.img'
        decompress_sparse_image(gz, cached)

        # Run two pipelines in parallel subprocesses, each with TZ set
        repo_root = Path(__file__).resolve().parent.parent.parent

        results = {}

        def run_pipeline(label, tz):
            env = os.environ.copy()
            env['TZ'] = tz
            env['PYTHONPATH'] = f'src:{repo_root}/test'
            r = subprocess.run(
                [sys.executable, '-m', 'unittest',
                 'test_full_auto_integration.TestFullAutoIntegration.test_full_pipeline',
                 '-v'],
                cwd=repo_root, env=env,
                capture_output=True, text=True, timeout=120)
            out = r.stdout + r.stderr
            # Check for failures
            failed = 'FAIL' in out and 'FAILED' in out
            results[label] = (r.returncode, failed, out)

        threads = [
            threading.Thread(target=run_pipeline, args=('A', 'UTC')),
            threading.Thread(target=run_pipeline, args=('B', 'Europe/Berlin')),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        for label in ('A', 'B'):
            rc, failed, out = results.get(label, (1, True, ''))
            self.assertEqual(
                rc, 0,
                f'Pipeline {label} failed with rc={rc}\n'
                f'{out[-500:]}')
            self.assertFalse(
                failed,
                f'Pipeline {label} test FAILED\n'
                f'{out[-1000:]}')
