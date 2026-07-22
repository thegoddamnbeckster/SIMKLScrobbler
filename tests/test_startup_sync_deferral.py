# -*- coding: utf-8 -*-
"""
Regression tests for the startup-sync / library-scan race condition.

Background (see changelog / commit history around 2026-07-22):
SimklService.run() used to gate the startup sync purely on
self._library_scan_in_progress, a flag only ever set True by the
onScanStarted monitor callback. That callback is missed if a video scan
begins before SimklMonitor is constructed - which happens in practice on
slower devices, where Kodi's boot-time library scan can start several
seconds before this addon finishes importing its modules. When missed,
the flag stayed False all along, so the startup sync fired immediately
and ran concurrently with the still-active scan, both hammering the same
video database.

_should_defer_startup_sync() closes that gap by also polling Kodi's live
scan state (Library.IsScanningVideo) instead of relying solely on the
callback having fired in time. These tests pin down that decision logic
in isolation, without needing to run the real service loop.
"""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 — side-effect: stubs xbmc/xbmcaddon/xbmcgui

from resources.lib.service import SimklService


class TestStartupSyncDeferral(unittest.TestCase):

    def setUp(self):
        self.service = SimklService()

    def test_no_scan_flag_and_live_check_false_does_not_defer(self):
        """Normal case: nothing scanning - startup sync should run immediately."""
        self.service._library_scan_in_progress = False
        with patch('resources.lib.service.xbmc.getCondVisibility', return_value=False) as cond:
            self.assertFalse(self.service._should_defer_startup_sync())
        cond.assert_called_once_with('Library.IsScanningVideo')

    def test_flag_already_set_defers_without_needing_live_check(self):
        """If onScanStarted *did* fire in time, that alone is sufficient."""
        self.service._library_scan_in_progress = True
        with patch('resources.lib.service.xbmc.getCondVisibility') as cond:
            self.assertTrue(self.service._should_defer_startup_sync())
        cond.assert_not_called()

    def test_missed_callback_is_caught_by_live_scan_check(self):
        """
        The actual bug being fixed: onScanStarted never fired (flag stayed
        False) but Kodi is genuinely still scanning. The live check must
        still catch this and defer.
        """
        self.service._library_scan_in_progress = False
        with patch('resources.lib.service.xbmc.getCondVisibility', return_value=True):
            self.assertTrue(self.service._should_defer_startup_sync())

    def test_live_check_exception_falls_back_to_not_deferring(self):
        """
        A broken/unavailable getCondVisibility call must never permanently
        stall sync-on-startup. Falling back to "not scanning" reproduces
        pre-fix behaviour in this edge case rather than a new failure mode
        (sync waiting forever for an onScanFinished that will never come).
        """
        self.service._library_scan_in_progress = False
        with patch('resources.lib.service.xbmc.getCondVisibility', side_effect=RuntimeError('boom')):
            self.assertFalse(self.service._should_defer_startup_sync())


if __name__ == '__main__':
    unittest.main()
