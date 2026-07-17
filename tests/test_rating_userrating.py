# -*- coding: utf-8 -*-
"""
Regression test: setting a rating via the rating dialog must update Kodi's
local library userrating immediately, not just SIMKL.

Report: "When I set a rating, the rating needs to actually be updated
immediately onto whatever I just rated. If you don't, it seems like no
update is being made." Removing a rating already cleared the local Kodi
userrating badge right away (_clear_kodi_userrating, called only from the
unrate branch); submitting a new rating patched the ratings cache and
notified SIMKL but never touched Kodi's local library, so the on-screen
badge stayed stale until the next full sync ran. Fixed by generalizing the
clear-only helper into _set_kodi_userrating(media_type, dbid, rating) and
calling it from both the submit and remove branches of prompt_for_rating().
"""

import sys
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 — side-effect: stubs xbmc/xbmcaddon/xbmcgui

import xbmc
from resources.lib.rating import RatingService


class TestSetKodiUserratingHelper(unittest.TestCase):
    """Direct unit tests for the RPC-issuing helper itself."""

    def setUp(self):
        xbmc.executeJSONRPC.reset_mock()
        self.service = RatingService(MagicMock())

    def test_movie_rpc_carries_the_given_rating(self):
        self.service._set_kodi_userrating('movie', 42, 8)
        call_json = xbmc.executeJSONRPC.call_args[0][0]
        self.assertIn('"movieid": 42', call_json)
        self.assertIn('"userrating": 8', call_json)
        self.assertIn('SetMovieDetails', call_json)

    def test_show_rpc_carries_the_given_rating(self):
        self.service._set_kodi_userrating('show', 7, 5)
        call_json = xbmc.executeJSONRPC.call_args[0][0]
        self.assertIn('"tvshowid": 7', call_json)
        self.assertIn('"userrating": 5', call_json)
        self.assertIn('SetTVShowDetails', call_json)

    def test_episode_resolves_to_show_rpc(self):
        """context.simkl resolves episodes to their parent show DBID before
        calling in, so 'episode' must use SetTVShowDetails, not episode RPC."""
        self.service._set_kodi_userrating('episode', 9, 10)
        call_json = xbmc.executeJSONRPC.call_args[0][0]
        self.assertIn('SetTVShowDetails', call_json)

    def test_zero_rating_is_a_valid_clear(self):
        self.service._set_kodi_userrating('movie', 1, 0)
        call_json = xbmc.executeJSONRPC.call_args[0][0]
        self.assertIn('"userrating": 0', call_json)

    def test_missing_dbid_is_a_noop(self):
        self.service._set_kodi_userrating('movie', None, 8)
        xbmc.executeJSONRPC.assert_not_called()


class TestPromptForRatingUpdatesKodiImmediately(unittest.TestCase):
    """End-to-end: submitting a rating through the dialog must reach
    _set_kodi_userrating with the actual selected rating, not just clear it
    (which was already covered) or skip it entirely (the bug)."""

    def setUp(self):
        xbmc.executeJSONRPC.reset_mock()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self._cache_path_patch = patch(
            "resources.lib.rating.get_ratings_cache_path",
            side_effect=lambda media_type: os.path.join(self._tmpdir.name, f"{media_type}.json"),
        )
        self._cache_path_patch.start()
        self.addCleanup(self._cache_path_patch.stop)

    def _make_service(self):
        api = MagicMock()
        api.add_rating.return_value = {"movies": {"rated": 1}}
        api.remove_rating.return_value = {"movies": {"unrated": 1}}
        service = RatingService(api)
        # Avoid a real network round-trip inside prompt_for_rating().
        service.get_current_rating = MagicMock(return_value=None)
        return service

    def _fake_dialog(self, submitted, selected_rating):
        dlg = MagicMock()
        dlg.doModal = MagicMock()
        dlg.submitted = submitted
        dlg.selected_rating = selected_rating
        return dlg

    def test_submitting_a_rating_updates_kodi_userrating(self):
        service = self._make_service()
        media_info = {
            'media_type': 'movie', 'title': 'Test Movie',
            'simkl_id': 123, 'kodi_dbid': 555,
        }
        with patch("resources.lib.rating.RatingDialog",
                   return_value=self._fake_dialog(True, 8)):
            result = service.prompt_for_rating(media_info)

        self.assertTrue(result)
        xbmc.executeJSONRPC.assert_called_once()
        call_json = xbmc.executeJSONRPC.call_args[0][0]
        self.assertIn('"movieid": 555', call_json)
        self.assertIn('"userrating": 8', call_json)

    def test_removing_a_rating_still_clears_kodi_userrating(self):
        """Guard against regressing the pre-existing unrate behavior while
        generalizing the helper."""
        service = self._make_service()
        media_info = {
            'media_type': 'movie', 'title': 'Test Movie',
            'simkl_id': 123, 'kodi_dbid': 555,
        }
        with patch("resources.lib.rating.RatingDialog",
                   return_value=self._fake_dialog(True, 0)):
            result = service.prompt_for_rating(media_info)

        self.assertTrue(result)
        xbmc.executeJSONRPC.assert_called_once()
        call_json = xbmc.executeJSONRPC.call_args[0][0]
        self.assertIn('"userrating": 0', call_json)

    def test_no_kodi_dbid_skips_rpc_without_erroring(self):
        """Ratings prompted from the after-playback path (no context-menu
        DBID available) must not attempt an RPC call."""
        service = self._make_service()
        media_info = {'media_type': 'movie', 'title': 'Test Movie', 'simkl_id': 123}
        with patch("resources.lib.rating.RatingDialog",
                   return_value=self._fake_dialog(True, 8)):
            result = service.prompt_for_rating(media_info)

        self.assertTrue(result)
        xbmc.executeJSONRPC.assert_not_called()


if __name__ == "__main__":
    unittest.main()
