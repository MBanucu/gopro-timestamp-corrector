"""Tests for the modification history logger."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import history
from exiftool_session import ExifToolSession


class TestHistory(unittest.TestCase):

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix='history_test_'))
        self.session = MagicMock(spec=ExifToolSession)
        self.session.dump_full_json.side_effect = (
            lambda fps: None if not fps else '[{"SourceFile": "test.txt", "FileSize": "0 bytes"}]')

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_begin_run_creates_directory(self):
        meta = {'fix_btime': 'exfat_raw'}
        run_dir = history.begin_run(self._tmp, meta)
        self.assertTrue(run_dir.is_dir())
        self.assertEqual(run_dir.parent, self._tmp / history.HISTORY_DIR_NAME)

    def test_begin_run_writes_run_json(self):
        meta = {'fix_btime': 'exfat_raw', 'note': 'test'}
        run_dir = history.begin_run(self._tmp, meta)
        run_path = run_dir / 'run.json'
        self.assertTrue(run_path.exists())
        data = json.loads(run_path.read_text())
        self.assertEqual(data['fix_btime'], 'exfat_raw')
        self.assertEqual(data['note'], 'test')
        self.assertIn('timestamp', data)

    def test_multiple_runs_are_append_only(self):
        run1 = history.begin_run(self._tmp, {'run': 1})
        run2 = history.begin_run(self._tmp, {'run': 2})
        self.assertNotEqual(run1, run2)
        self.assertTrue(run1.exists())
        self.assertTrue(run2.exists())

    def test_capture_before_creates_file(self):
        meta = {'fix_btime': 'exfat_raw'}
        run_dir = history.begin_run(self._tmp, meta)
        file1 = self._tmp / 'test.txt'
        file1.write_text('hello')
        history.capture_before(self.session, run_dir, [file1])
        before = run_dir / 'before.json'
        self.assertTrue(before.exists())
        data = json.loads(before.read_text())
        self.assertIsInstance(data, list)

    def test_capture_after_creates_file(self):
        meta = {'fix_btime': 'exfat_raw'}
        run_dir = history.begin_run(self._tmp, meta)
        file1 = self._tmp / 'test.txt'
        file1.write_text('hello')
        history.capture_after(self.session, run_dir, [file1])
        after = run_dir / 'after.json'
        self.assertTrue(after.exists())

    def test_finalize_run_updates_summary(self):
        meta = {'fix_btime': 'exfat_raw'}
        run_dir = history.begin_run(self._tmp, meta)
        history.finalize_run(run_dir, written=5, skipped=2, errors=['file1 failed'])
        data = json.loads((run_dir / 'run.json').read_text())
        self.assertEqual(data['summary']['written'], 5)
        self.assertEqual(data['summary']['skipped'], 2)
        self.assertEqual(data['summary']['errors'], ['file1 failed'])

    def test_capture_empty_list_does_nothing(self):
        meta = {}
        run_dir = history.begin_run(self._tmp, meta)
        history.capture_before(self.session, run_dir, [])
        self.assertFalse((run_dir / 'before.json').exists())
        history.capture_after(self.session, run_dir, [])
        self.assertFalse((run_dir / 'after.json').exists())


if __name__ == '__main__':
    unittest.main()
