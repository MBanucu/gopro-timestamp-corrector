"""H22: Spawn two subprocesses, each runs ExifTool with individual sessions,
and verify directory entry corruption appears on both mounts.

Usage: PYTHONPATH=src python3 -m unittest test.hypothesis.test_h22_subprocess_corruption -v
"""
import os, subprocess, sys, threading, unittest, json, tempfile
from pathlib import Path

_BD = str(Path(__file__).resolve().parent.parent.parent / 'src')
if _BD not in os.environ.get('PYTHONPATH', ''):
    os.environ.setdefault('PYTHONPATH', _BD)


SUBPROCESS_SCRIPT = r'''import sys, os, json
# Add repo root to path (parent of test/, src/)
sys.path.insert(0, "{repo}")

from exiftool_session import ExifToolSession
from datetime import datetime, timezone
from pathlib import Path
from test.shared import decompress_sparse_image, prepare_sparse_image
from test.shared import setup_loop_device, teardown_loop_device
from strategies.exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps

gz = Path("{gz}")
cached = Path("{cached}")
decompress_sparse_image(gz, cached)
work, img = prepare_sparse_image(gz, prefix="h22_")
loop, mnt = setup_loop_device(str(img))
target = Path(mnt) / "DCIM" / "100GOPRO"
files = sorted(target.glob("*"))

io = ExfatRawIO(); fs = ExfatRawFilesystem(io); ops = ExfatRawOps(io, fs)
before = {{f.name: ops.read_mtime_raw(str(f)) for f in files}}

# Individual ExifToolSession per file (the trigger pattern)
for f in files:
    with ExifToolSession() as sess:
        sess.write_embedded(f, datetime.now(timezone.utc))

after = {{f.name: ops.read_mtime_raw(str(f)) for f in files}}
corrupted = {{k: v for k, v in after.items() if v != before.get(k)}}
print(json.dumps(corrupted), flush=True)

teardown_loop_device(loop, mnt)
import shutil; shutil.rmtree(work, ignore_errors=True)
'''


class H22_SubprocessCorruption(unittest.TestCase):

    def test_two_subprocesses_corruption(self):
        repo = Path(__file__).resolve().parent.parent.parent
        src = str(repo / 'src')
        test_dir = str(repo / 'test')
        gz = str(repo / 'test' / 'sdcard.img.gz')
        cached = str(repo / 'test' / 'sdcard.img')

        if not Path(gz).exists():
            self.skipTest('sdcard.img.gz not found')

        # Write the script
        script_content = SUBPROCESS_SCRIPT.format(
            repo=str(repo), gz=gz, cached=cached)
        script_path = Path(tempfile.mktemp(suffix='.py'))
        script_path.write_text(script_content)

        env = os.environ.copy()
        # Don't set PYTHONPATH — the script sets it via sys.path.insert

        try:
            # Spawn two subprocesses simultaneously
            procs = []
            for _ in range(2):
                p = subprocess.Popen(
                    [sys.executable, str(script_path)],
                    cwd=repo, env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True)
                procs.append(p)

            # Collect results
            all_corrupted = {}
            errors = []
            for i, p in enumerate(procs):
                out, err = p.communicate(timeout=120)
                if err.strip():
                    errors.append(f'Proc {i} stderr: {err.strip()[:300]}')
                if out.strip():
                    try:
                        data = json.loads(out.strip())
                        if data:
                            all_corrupted[f'proc_{i}'] = data
                    except json.JSONDecodeError as e:
                        errors.append(f'Proc {i} JSON error: {e} out={out.strip()[:100]}')
                if p.returncode != 0:
                    errors.append(f'Proc {i} rc={p.returncode}')

            if errors:
                self.fail('\n'.join(errors))

            total = sum(len(v) for v in all_corrupted.values())
            self.assertGreater(
                total, 0,
                f'Expected corruptions with subprocesses + individual ExifTool sessions, '
                f'got 0. This means the kernel exFAT driver bug did not trigger.\n'
                f'Full output: {all_corrupted}')
            print(f'[subprocesses] {total} corruptions across {len(all_corrupted)} processes')

        finally:
            if script_path.exists():
                script_path.unlink()
