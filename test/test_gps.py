import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from pathlib import Path
import media

class TestGPS(unittest.TestCase):
    @patch('subprocess.run')
    def test_read_gps_time_parsing(self, mock_run):
        # Mock exiftool output for GX010066.MP4
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="2026:05:14 13:41:45.600\n2026:05:14 13:41:45.600\n"
        )
        
        dt = media.read_gps_time("fake.mp4")
        self.assertEqual(dt, datetime(2026, 5, 14, 13, 41, 45, 600000))
        
        # Test with Z suffix
        mock_run.return_value.stdout = "2021:03:11 12:51:00.199Z\n"
        dt = media.read_gps_time("fake.mp4")
        self.assertEqual(dt, datetime(2021, 3, 11, 12, 51, 0, 199000))

    @patch('subprocess.run')
    def test_read_gps_time_no_data(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        dt = media.read_gps_time("fake.mp4")
        self.assertIsNone(dt)
        
        mock_run.return_value.returncode = 1
        dt = media.read_gps_time("fake.mp4")
        self.assertIsNone(dt)

if __name__ == '__main__':
    unittest.main()
