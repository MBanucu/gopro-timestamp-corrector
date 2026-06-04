"""Full end-to-end integration test through the shared ExifTool server."""
import shutil
import sys
import time
import unittest
from datetime import timedelta
from pathlib import Path


class TestFullAutoIntegration(unittest.TestCase):
    """Mount sdcard image, auto calibrate, apply all, verify metadata through server."""

    @classmethod
    def setUpClass(cls):
        cls._work_dir = None
        cls.mount_point = None
        cls.loop_dev = None
        cls.target = None

        gz_path = Path(__file__).parent / 'sdcard.img.gz'
        if not gz_path.exists():
            raise unittest.SkipTest(f'Compressed image not found at {gz_path}')

        from test.shared import decompress_sparse_image, prepare_sparse_image, \
            setup_loop_device, teardown_loop_device

        cached = Path(__file__).parent / 'sdcard.img'
        decompress_sparse_image(gz_path, cached)

        cls._work_dir, cls.img_path = prepare_sparse_image(gz_path)
        cls.loop_dev, cls.mount_point = setup_loop_device(cls.img_path)
        cls.addClassCleanup(teardown_loop_device, cls.loop_dev, cls.mount_point)
        cls.addClassCleanup(shutil.rmtree, cls._work_dir, ignore_errors=True)

        cls.target = Path(cls.mount_point) / 'DCIM' / '100GOPRO'
        if not cls.target.exists():
            raise unittest.SkipTest(f'{cls.target} not found')

    def test_full_pipeline(self):
        """Run full correction through the server, verify every metadata field."""
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))

        import media
        from exiftool_session import ExifToolSession
        import analysis as an_mod
        from plan import CorrectionPlan, Planner, PlanBuilder
        from resolve import weighted_median_delta
        from exfat_raw import ExfatRawIO, ExfatRawFilesystem, ExfatRawOps

        files = media.collect(self.target)
        self.assertGreater(len(files), 0, 'Should find video files on the image')

        with ExifToolSession() as session:
            wall_start = time.perf_counter()
            t0 = wall_start

            before_exif = session.read_tags_batch(files)
            t_read = time.perf_counter() - t0

            t0 = time.perf_counter()
            accuracy = session.read_gps_accuracy_batch(files)
            t_acc = time.perf_counter() - t0

            pairs = []
            for f in files:
                embedded, gps = before_exif.get(f, (None, None))
                if embedded is None or gps is None:
                    continue
                acc = accuracy.get(f, 99.99)
                if acc is None or acc >= 25.0 or acc == 99.99:
                    continue
                delta = gps - embedded
                weight = 1.0 / (acc + 1.0)
                pairs.append((delta, weight))

            self.assertGreaterEqual(
                len(pairs), 1,
                'Need at least one file with valid GPS fix')

            deltas = [p[0] for p in pairs]
            weights = [p[1] for p in pairs]
            median = weighted_median_delta(deltas, weights)
            self.assertIsNotNone(median, 'Weighted median delta should be computable')
            t_auto = time.perf_counter() - t0

            io = ExfatRawIO()
            fs = ExfatRawFilesystem(io)
            ops = ExfatRawOps(io, fs)
            before_raw = {
                f: (ops.read_mtime_raw(str(f)), ops.read_btime_raw(str(f)))
                for f in files
            }

            t0 = time.perf_counter()
            result = an_mod.analyze(session, self.target)
            self.assertGreater(result.total_files, 0, 'Analysis should find files')

            plan = CorrectionPlan(analysis=result, manual_delta=median)
            plan.set_all_manual()
            t_plan = time.perf_counter() - t0

            planner = Planner(
                dry_run=False,
                fix_btime=True,
                btime_methods=['exfat_raw'],
            )

            builder = PlanBuilder()
            instructions = builder.build(planner, plan, self.target)

            t0 = time.perf_counter()
            outcome = builder.execute(instructions, session=session)
            t_write = time.perf_counter() - t0

            self.assertEqual(
                outcome['exit_code'], 0,
                f'Pipeline failed: {outcome["errors"]}')
            self.assertEqual(
                len(outcome.get('errors', [])), 0,
                f'Write errors: {outcome["errors"]}')
            self.assertEqual(
                outcome['written'], len(files) * 3,
                f'Expected {len(files) * 3} writes '
                f'(embedded + mtime + btime), got {outcome["written"]}')

            t0 = time.perf_counter()
            after_exif = session.read_tags_batch(files)
            t_verify = time.perf_counter() - t0

            exif_tolerance = timedelta(seconds=2)
            raw_tolerance = timedelta(seconds=6)
            for f in files:
                name = f.name
                before = before_exif.get(f, (None, None))
                after = after_exif.get(f, (None, None))
                expected_exif = (before[0] + median) if before[0] else None
                actual_exif = after[0] if after else None
                if expected_exif is not None and actual_exif is not None:
                    diff = abs((actual_exif - expected_exif).total_seconds())
                    self.assertLess(
                        diff, exif_tolerance.total_seconds(),
                        f'{name} exif: expected {expected_exif}, got {actual_exif} '
                        f'(diff {diff:.2f}s)')
                elif before[0] is None:
                    print(f'  {name}: no embedded time before correction (skipping exif verify)')

                if before[0] is not None:
                    expected_ts = int((before[0] + median).timestamp())
                else:
                    orig_mtime, _ = before_raw.get(f, (None, None))
                    if orig_mtime is not None:
                        expected_ts = int(orig_mtime + median.total_seconds())
                    else:
                        expected_ts = None

                if expected_ts is not None:
                    raw_mtime = ops.read_mtime_raw(str(f))
                    self.assertIsNotNone(
                        raw_mtime,
                        f'{name}: mtime raw readback returned None')
                    mtime_diff = abs(raw_mtime - expected_ts)
                    self.assertLess(
                        mtime_diff, raw_tolerance.total_seconds(),
                        f'{name} mtime (raw): expected ts {expected_ts}, '
                        f'got {raw_mtime} (diff {mtime_diff:.1f}s)')

                    raw_btime = ops.read_btime_raw(str(f))
                    self.assertIsNotNone(
                        raw_btime,
                        f'{name}: btime raw readback returned None')
                    btime_diff = abs(raw_btime - expected_ts)
                    self.assertLess(
                        btime_diff, raw_tolerance.total_seconds(),
                        f'{name} btime (raw): expected ts {expected_ts}, '
                        f'got {raw_btime} (diff {btime_diff:.1f}s)')

            total_t = time.perf_counter() - wall_start
            print(f'\n  TIMING')
            print(f'  │ read tags:           {t_read:.2f}s')
            print(f'  │ read accuracy:       {t_acc:.2f}s')
            print(f'  │ auto calibrate:      {t_auto:.2f}s')
            print(f'  │ build plan:          {t_plan:.2f}s')
            print(f'  │ write corrections:   {t_write:.2f}s')
            print(f'  │ verify metadata:     {t_verify:.2f}s')
            print(f'  │ {"─" * 30}')
            print(f'  │ total:               {total_t:.2f}s')


if __name__ == '__main__':
    unittest.main()
