"""Tests for btime method registration and project-level strategies.

These test the project's own abstraction layer (btime.resolve_method,
ExfatRawReadStrategy) rather than the external exfat-raw library directly.
"""

import unittest


class TestExfatRawRegistration(unittest.TestCase):
    """btime module: method resolution and processing-after checks."""

    def test_exfat_raw_is_registered_as_method(self):
        from btime import resolve_method, needs_processing_after
        self.assertEqual(resolve_method('exfat_raw', 'exfat'), 'exfat_raw')
        self.assertTrue(needs_processing_after('exfat_raw'))

    def test_exfat_raw_read_strategy_read_btime_raw(self):
        self.skipTest('requires loop device setup — see exfat-raw package tests')


if __name__ == '__main__':
    unittest.main()
