import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import analysis


class TestGroupKey(unittest.TestCase):
    def test_standard_gopro_stem(self):
        self.assertEqual(analysis._group_key(Path('GX010063.MP4')), '010063')
        self.assertEqual(analysis._group_key(Path('GL010063.LRV')), '010063')

    def test_different_prefix(self):
        self.assertEqual(analysis._group_key(Path('GH010001.MP4')), '010001')

    def test_long_number(self):
        self.assertEqual(analysis._group_key(Path('GX12345678.MP4')), '12345678')

    def test_no_digits(self):
        self.assertIsNone(analysis._group_key(Path('video.mp4')))

    def test_empty_stem(self):
        self.assertIsNone(analysis._group_key(Path('.hidden')))


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.fake_files = [
            Path('/d/GL010063.LRV'),
            Path('/d/GX010063.MP4'),
            Path('/d/GX010063.THM'),
            Path('/d/GL010064.LRV'),
            Path('/d/GX010064.MP4'),
            Path('/d/GX010065.MP4'),  # no LRV or THM pair
        ]

    @patch('media.collect')
    @patch('media.read_mtime')
    @patch('media.read_embedded')
    @patch('media.read_gps_time')
    def test_analyze_groups_by_stem(self, mock_gps, mock_emb, mock_mtime, mock_collect):
        mock_collect.return_value = self.fake_files
        mock_mtime.return_value = datetime(2026, 5, 14, 18, 0, 0)
        mock_emb.return_value = datetime(2026, 5, 14, 16, 0, 0)
        mock_gps.return_value = datetime(2021, 3, 11, 12, 51, 0)

        result = analysis.analyze('/d')
        self.assertEqual(result.total_files, 6)
        self.assertEqual(len(result.sets), 3)

        set_ids = [s.id for s in result.sets]
        self.assertEqual(set_ids, ['010063', '010064', '010065'])

    @patch('media.collect')
    @patch('media.read_mtime')
    @patch('media.read_embedded')
    @patch('media.read_gps_time')
    def test_set_has_gps(self, mock_gps, mock_emb, mock_mtime, mock_collect):
        mock_collect.return_value = self.fake_files
        mock_mtime.return_value = datetime(2026, 5, 14, 18, 0, 0)
        mock_emb.return_value = datetime(2026, 5, 14, 16, 0, 0)
        mock_gps.side_effect = lambda p: datetime(2021, 3, 11, 12, 51, 0) if '063' in str(p) else None

        result = analysis.analyze('/d')
        set_063 = next(s for s in result.sets if s.id == '010063')
        set_064 = next(s for s in result.sets if s.id == '010064')

        self.assertTrue(set_063.has_any_gps)
        self.assertFalse(set_064.has_any_gps)

    @patch('media.collect')
    @patch('media.read_mtime')
    @patch('media.read_embedded')
    @patch('media.read_gps_time')
    def test_set_kind(self, mock_gps, mock_emb, mock_mtime, mock_collect):
        mock_collect.return_value = self.fake_files
        mock_mtime.return_value = datetime(2026, 5, 14, 18, 0, 0)
        mock_emb.return_value = datetime(2026, 5, 14, 16, 0, 0)
        mock_gps.return_value = None

        result = analysis.analyze('/d')
        set_063 = next(s for s in result.sets if s.id == '010063')
        set_065 = next(s for s in result.sets if s.id == '010065')

        self.assertEqual(set_063.kind, 'MP4+LRV+THM')
        self.assertEqual(set_065.kind, 'MP4')

    @patch('media.collect')
    @patch('media.read_mtime')
    @patch('media.read_embedded')
    @patch('media.read_gps_time')
    def test_empty_directory(self, mock_gps, mock_emb, mock_mtime, mock_collect):
        mock_collect.return_value = []
        result = analysis.analyze('/empty')
        self.assertEqual(result.total_files, 0)
        self.assertEqual(len(result.sets), 0)

    @patch('media.collect')
    @patch('media.read_mtime')
    @patch('media.read_embedded')
    @patch('media.read_gps_time')
    def test_file_info_values(self, mock_gps, mock_emb, mock_mtime, mock_collect):
        mock_collect.return_value = [Path('/d/GX010001.MP4')]
        mock_mtime.return_value = datetime(2026, 5, 14, 18, 30, 0)
        mock_emb.return_value = datetime(2026, 5, 14, 16, 0, 0)
        mock_gps.return_value = None

        result = analysis.analyze('/d')
        fi = result.sets[0].files[0]
        self.assertEqual(fi.stem, 'GX010001')
        self.assertEqual(fi.ext, '.mp4')
        self.assertEqual(fi.mtime, datetime(2026, 5, 14, 18, 30, 0))
        self.assertEqual(fi.embedded_time, datetime(2026, 5, 14, 16, 0, 0))


if __name__ == '__main__':
    unittest.main()
