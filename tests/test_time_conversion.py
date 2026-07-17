# -*- coding: utf-8 -*-
"""
Regression tests for _kodi_time_to_utc_iso() / _local_utc_offset() in sync.py.

Live kodi.log from an NVIDIA Shield (Kodi 21.2, Android TV) showed
_kodi_time_to_utc_iso() failing on nearly every episode's lastplayed
timestamp with "'NoneType' object is not callable" — the exact failure
mode the function's own docstring already claimed to have fixed by
switching to time.mktime(), which turned out not to hold on that device's
Kodi Python build (time.mktime() itself is broken there). The fix derives
the local/UTC offset from time.localtime()/time.gmtime() instead, since
those are confirmed working elsewhere in this codebase (e.g. the
time.ctime() calls used for last-sync-time logging).
"""

import sys
import os
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 — side-effect: stubs xbmc/xbmcaddon/xbmcgui

from resources.lib.sync import _kodi_time_to_utc_iso, _local_utc_offset


class TestKodiTimeToUtcIso(unittest.TestCase):

    def test_survives_broken_time_mktime(self):
        """Must not depend on time.mktime() at all - on the reported Android
        build it is stubbed to None, and calling it raises
        "'NoneType' object is not callable"."""
        with patch("time.mktime", None):
            result = _kodi_time_to_utc_iso("2026-07-12 16:39:15")
        self.assertIsNotNone(result)
        self.assertTrue(result.endswith("Z"))

    def test_applies_local_utc_offset(self):
        """The converted UTC timestamp must differ from the naive local
        string by exactly the current local/UTC offset."""
        offset = _local_utc_offset()
        result = _kodi_time_to_utc_iso("2026-07-12 16:39:15")
        expected_dt = (
            time.strptime("2026-07-12 16:39:15", "%Y-%m-%d %H:%M:%S")
        )
        import datetime as dt
        naive = dt.datetime(*expected_dt[:6])
        expected = (naive - offset).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(result, expected)

    def test_malformed_timestamp_returns_none(self):
        self.assertIsNone(_kodi_time_to_utc_iso("not-a-date"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(_kodi_time_to_utc_iso(""))


if __name__ == "__main__":
    unittest.main()
