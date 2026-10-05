# -*- coding: utf-8 -*-
"""
An episode must be scrobbled with its SHOW's ids, never its own (2026-10-05).

The VideoInfoTag of a playing episode carries the episode's imdb/tvdb/tmdb ids. Sent to SIMKL as the
show's they resolve to nothing, SIMKL falls back to the bare show title, and "Supernatural" matched the
2011 anime of the same name instead of the 2005 series -- the household's episodes were recorded against
the anime. The show's own ids are read from Kodi's library (episode -> tvshowid -> TV show details).
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401

import xbmc
from resources.lib import service as service_mod
from resources.lib.scrobbler import SimklScrobbler
from resources.lib.utils import get_show_identity_for_episode


def _rpc(episode=None, show=None):
    """An executeJSONRPC fake answering the two library calls."""
    def fake(request):
        method = json.loads(request)["method"]
        if method == "VideoLibrary.GetEpisodeDetails":
            return json.dumps({"result": {"episodedetails": episode}} if episode else {"error": {}})
        if method == "VideoLibrary.GetTVShowDetails":
            return json.dumps({"result": {"tvshowdetails": show}} if show else {"error": {}})
        return "{}"
    return fake


SUPERNATURAL = {"uniqueid": {"imdb": "tt0460681", "tvdb": "78901", "tmdb": "1622"}, "year": 2005}


class TestGetShowIdentity(unittest.TestCase):

    def tearDown(self):
        xbmc.executeJSONRPC = MagicMock(return_value='{"result": {}}')

    def test_returns_the_shows_own_ids_and_year(self):
        xbmc.executeJSONRPC = _rpc({"tvshowid": 7}, SUPERNATURAL)
        self.assertEqual(get_show_identity_for_episode(123),
                         {"ids": {"imdb": "tt0460681", "tvdb": "78901", "tmdb": "1622"}, "year": 2005})

    def test_finds_an_imdb_id_filed_under_another_key(self):
        xbmc.executeJSONRPC = _rpc({"tvshowid": 7}, {"uniqueid": {"unknown": "tt0460681"}, "year": 0})
        self.assertEqual(get_show_identity_for_episode(123), {"ids": {"imdb": "tt0460681"}, "year": None})

    def test_not_in_the_library_gives_none(self):
        self.assertIsNone(get_show_identity_for_episode(-1))
        self.assertIsNone(get_show_identity_for_episode(0))
        self.assertIsNone(get_show_identity_for_episode(None))

    def test_unknown_episode_or_show_gives_none(self):
        xbmc.executeJSONRPC = _rpc(None, None)
        self.assertIsNone(get_show_identity_for_episode(123))
        xbmc.executeJSONRPC = _rpc({"tvshowid": 7}, None)
        self.assertIsNone(get_show_identity_for_episode(123))

    def test_a_json_rpc_failure_gives_none_rather_than_raising(self):
        xbmc.executeJSONRPC = MagicMock(side_effect=RuntimeError("kodi gone"))
        self.assertIsNone(get_show_identity_for_episode(123))


class TestVideoDataUsesShowIds(unittest.TestCase):

    def tearDown(self):
        xbmc.executeJSONRPC = MagicMock(return_value='{"result": {}}')

    def _player(self, dbid):
        tag = MagicMock()
        tag.getMediaType.return_value = "episode"
        tag.getTitle.return_value = "Bugs"
        tag.getYear.return_value = 2005
        tag.getIMDBNumber.return_value = "tt0713612"            # the EPISODE's imdb id
        tag.getUniqueID.side_effect = lambda k: {"imdb": "tt0713612", "tmdb": "99887766"}.get(k, "")
        tag.getTVShowTitle.return_value = "Supernatural"
        tag.getSeason.return_value = 1
        tag.getEpisode.return_value = 8
        tag.getDbId.return_value = dbid
        tag.getPlayCount.return_value = 0
        player = service_mod.SimklPlayer(action=None)
        player._current_file = "/tv/Supernatural/S01E08.mkv"
        player.getVideoInfoTag = MagicMock(return_value=tag)
        return player

    def test_library_episode_gets_the_shows_ids_not_its_own(self):
        xbmc.executeJSONRPC = _rpc({"tvshowid": 7}, SUPERNATURAL)

        data = self._player(dbid=123)._get_video_data()

        self.assertEqual((data["imdb_id"], data["tvdb_id"], data["tmdb_id"]), ("tt0460681", "78901", "1622"))
        self.assertEqual(data["show_year"], 2005)

    def test_episode_outside_the_library_keeps_whatever_its_tag_says(self):
        data = self._player(dbid=-1)._get_video_data()

        self.assertEqual(data["imdb_id"], "tt0713612")
        self.assertNotIn("show_year", data)

    def test_show_ids_reach_simkl_as_the_show_object(self):
        api = MagicMock()
        scrobbler = SimklScrobbler(api)
        info = scrobbler._identify_episode({
            "type": "episode", "show_title": "Supernatural", "season": 1, "episode": 8,
            "imdb_id": "tt0460681", "tvdb_id": "78901", "tmdb_id": "1622", "show_year": 2005,
        })
        self.assertEqual(info["show"]["ids"], {"imdb": "tt0460681", "tvdb": 78901, "tmdb": 1622})
        api.search_tv.assert_not_called()


class TestTitleSearchPrefersTheShowsYear(unittest.TestCase):

    def test_picks_the_result_from_the_premiere_year(self):
        api = MagicMock()
        api.search_tv.return_value = [
            {"title": "Supernatural", "year": 2011, "ids": {"simkl": 38876}},
            {"title": "Supernatural", "year": 2005, "ids": {"simkl": 8650}},
        ]
        info = SimklScrobbler(api)._identify_episode(
            {"type": "episode", "show_title": "Supernatural", "season": 1, "episode": 8, "show_year": 2005})
        self.assertEqual(info["show"]["ids"], {"simkl": 8650})

    def test_without_a_year_the_first_result_is_used_as_before(self):
        api = MagicMock()
        api.search_tv.return_value = [{"title": "A", "year": 1999, "ids": {"simkl": 1}},
                                      {"title": "A", "year": 2005, "ids": {"simkl": 2}}]
        info = SimklScrobbler(api)._identify_episode(
            {"type": "episode", "show_title": "A", "season": 1, "episode": 1})
        self.assertEqual(info["show"]["ids"], {"simkl": 1})


if __name__ == "__main__":
    unittest.main()
