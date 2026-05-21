import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock
from exiftool_session import ExifToolSession


class TestGPSParsing(unittest.TestCase):
    """Test ExifToolSession GPS parsing by mocking the underlying exiftool process."""

    def _make_session(self, mock_helper_cls):
        mock_helper = mock_helper_cls.return_value
        session = ExifToolSession(helper=mock_helper)
        return session, mock_helper

    @patch('exiftool_session.ExifToolHelper')
    def test_read_gps_time_parsing(self, mock_helper_cls):
        session, mock_helper = self._make_session(mock_helper_cls)
        mock_helper.execute.return_value = (
            "2026:05:14 13:41:45.600\n2026:05:14 13:41:45.600\n"
        )
        dt = session.read_gps_time("fake.mp4")
        self.assertEqual(
            dt, datetime(2026, 5, 14, 13, 41, 45, 600000, tzinfo=timezone.utc))

        # Test with Z suffix
        mock_helper.execute.return_value = "2021:03:11 12:51:00.199Z\n"
        dt = session.read_gps_time("fake.mp4")
        self.assertEqual(
            dt, datetime(2021, 3, 11, 12, 51, 0, 199000, tzinfo=timezone.utc))

    @patch('exiftool_session.ExifToolHelper')
    def test_read_gps_time_no_data(self, mock_helper_cls):
        session, mock_helper = self._make_session(mock_helper_cls)
        mock_helper.execute.return_value = ""
        dt = session.read_gps_time("fake.mp4")
        self.assertIsNone(dt)

    @patch('exiftool_session.ExifToolHelper')
    def test_read_tags_batch(self, mock_helper_cls):
        session, mock_helper = self._make_session(mock_helper_cls)
        mock_helper.execute.return_value = (
            '[{"SourceFile":"/fake/GX010066.MP4",'
            '"CreateDate":"2026:05:14 13:41:45",'
            '"GPSDateTime":"2026:05:14 11:41:45"}]\n'
        )
        result = session.read_tags_batch([Path('/fake/GX010066.MP4')])
        path = Path('/fake/GX010066.MP4')
        self.assertIn(path, result)
        embedded, gps = result[path]
        self.assertEqual(
            embedded, datetime(2026, 5, 14, 13, 41, 45, tzinfo=timezone.utc))
        self.assertEqual(
            gps, datetime(2026, 5, 14, 11, 41, 45, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
