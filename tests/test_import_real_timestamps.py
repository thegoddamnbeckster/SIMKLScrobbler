# -*- coding: utf-8 -*-
"""
Importing watched state from SIMKL must never invent a watch time (2026-10-17).

The import used to mark an item watched with a bare playcount write. Kodi stamps "now" as lastplayed
on such a write, so every item SIMKL said was watched (but Kodi did not yet know) got a fake
last-played at the moment of the sync -- on 2026-10-13 that was Vision's Kodi startup, and Onslaught,
finished days earlier on another device, read as a brand-new play and was pulled into Chronicle as a
rewatch. The import now writes SIMKL's own real time as lastplayed (movies: last_watched_at,
episodes: watched_at, requested with episode_watched_at=yes), and when SIMKL has no real time it
writes the playcount alone: the item is still marked watched, but nothing is invented.
"""
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 -- side-effect: stubs xbmc & friends

from resources.lib import sync as sync_mod
from resources.lib.api import SimklAPI
from resources.lib.sync import (
    SyncManager, _kodi_time_to_utc_iso, _utc_iso_to_kodi_time, _watched_properties,
)

SIMKL_TIME = "2026-10-11T10:57:59Z"


def _manager(kodi_movies=None, kodi_shows=None, kodi_episodes=None, simkl_movies=None,
             simkl_watching=None, simkl_completed=None):
    """A SyncManager with SIMKL and Kodi replaced by fakes; returns it with the recorded Kodi writes."""
    mgr = object.__new__(SyncManager)
    mgr.progress_dialog = None
    mgr.cancelled = False
    mgr.stats = {"errors": 0}
    mgr._last_tvshow_empty_excluded = 0
    mgr.api = MagicMock()

    def get_all_items(media_type, status, **kwargs):
        if media_type == "movies":
            return simkl_movies or []
        return (simkl_watching if status == "watching" else simkl_completed) or []

    mgr.api.get_all_items.side_effect = get_all_items
    mgr.get_kodi_movies = lambda: kodi_movies or []
    mgr.get_kodi_tvshows = lambda: kodi_shows or {}
    mgr.get_kodi_episodes = lambda: kodi_episodes or []
    mgr.writes = []

    def kodi_rpc(method, params=None):
        mgr.writes.append((method, params))
        return {}

    mgr._kodi_rpc = kodi_rpc
    return mgr


def _watch_writes(mgr, method):
    return [p for m, p in mgr.writes if m == method and p.get("playcount", 0) > 0]


class TestUtcIsoToKodiTime(unittest.TestCase):

    def test_is_the_inverse_of_kodi_time_to_utc_iso(self):
        for kodi_time in ("2026-10-11 04:57:59", "2026-10-13 16:52:14", "2026-10-16 22:19:45"):
            self.assertEqual(kodi_time, _utc_iso_to_kodi_time(_kodi_time_to_utc_iso(kodi_time)))

    def test_converts_by_the_local_offset(self):
        epoch = 1_791_716_279  # 2026-10-11 10:57:59 UTC
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch)),
                         _utc_iso_to_kodi_time(SIMKL_TIME))

    def test_survives_broken_time_mktime(self):
        # time.mktime is stubbed to None on some Android Kodi builds.
        original = time.mktime
        time.mktime = None
        try:
            self.assertIsNotNone(_utc_iso_to_kodi_time(SIMKL_TIME))
        finally:
            time.mktime = original

    def test_missing_or_garbage_is_none(self):
        for bad in (None, "", "not-a-date", 12345):
            self.assertIsNone(_utc_iso_to_kodi_time(bad))


class TestWorksWithoutStrptime(unittest.TestCase):
    """Kodi's embedded Python raises "'NoneType' object is not callable" from datetime.strptime when it is first
    called off the main thread. Vision's first run of 7.9.21 hit it ~3,000 times; the conversions failed, the import
    wrote bare playcounts, and Kodi stamped the current time on the imported movies."""

    def setUp(self):
        class NoStrptime(sync_mod.datetime):
            strptime = None
        self._original = sync_mod.datetime
        sync_mod.datetime = NoStrptime

    def tearDown(self):
        sync_mod.datetime = self._original

    def test_utc_iso_to_kodi_time(self):
        self.assertEqual(_utc_iso_to_kodi_time(SIMKL_TIME), self._original_result(SIMKL_TIME))
        self.assertIsNotNone(_utc_iso_to_kodi_time(SIMKL_TIME))

    def test_kodi_time_to_utc_iso(self):
        self.assertIsNotNone(_kodi_time_to_utc_iso("2026-10-11 04:57:59"))

    def test_the_two_conversions_still_round_trip(self):
        self.assertEqual("2026-10-11 04:57:59", _utc_iso_to_kodi_time(_kodi_time_to_utc_iso("2026-10-11 04:57:59")))

    def _original_result(self, iso):
        epoch = 1_791_716_279  # 2026-10-11 10:57:59 UTC
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


class TestWatchedProperties(unittest.TestCase):

    def test_a_real_time_becomes_lastplayed(self):
        props = _watched_properties(1, SIMKL_TIME)
        self.assertEqual(1, props["playcount"])
        self.assertEqual(_utc_iso_to_kodi_time(SIMKL_TIME), props["lastplayed"])

    def test_no_time_writes_the_playcount_alone(self):
        self.assertEqual({"playcount": 1}, _watched_properties(1, None))
        self.assertEqual({"playcount": 1}, _watched_properties(1, "garbage"))

    def test_unmarking_never_sets_lastplayed(self):
        self.assertEqual({"playcount": 0}, _watched_properties(0, SIMKL_TIME))


class TestMovieImport(unittest.TestCase):

    def _movie(self, last_watched_at):
        item = {"movie": {"title": "Onslaught", "year": 2026, "ids": {"imdb": "tt32268691"}}}
        if last_watched_at is not None:
            item["last_watched_at"] = last_watched_at
        return item

    def _kodi_movie(self):
        return {"movieid": 7, "title": "Onslaught", "year": 2026, "playcount": 0,
                "uniqueid": {"imdb": "tt32268691"}}

    def test_marks_watched_with_simkls_real_time(self):
        mgr = _manager(kodi_movies=[self._kodi_movie()], simkl_movies=[self._movie(SIMKL_TIME)])
        mgr.import_movies_from_simkl()
        writes = _watch_writes(mgr, "VideoLibrary.SetMovieDetails")
        self.assertEqual(1, len(writes))
        self.assertEqual(7, writes[0]["movieid"])
        self.assertEqual(_utc_iso_to_kodi_time(SIMKL_TIME), writes[0]["lastplayed"])

    def test_with_no_real_time_it_is_still_marked_watched_but_nothing_is_invented(self):
        mgr = _manager(kodi_movies=[self._kodi_movie()], simkl_movies=[self._movie(None)])
        mgr.import_movies_from_simkl()
        writes = _watch_writes(mgr, "VideoLibrary.SetMovieDetails")
        self.assertEqual([{"movieid": 7, "playcount": 1}], writes)

    def test_never_marks_watched_without_either_a_time_or_an_explicit_no_time(self):
        # The guard against the original bug: a watched write must carry lastplayed whenever SIMKL gave a time.
        for stamp in (SIMKL_TIME, "2026-01-02T03:04:05Z", None):
            mgr = _manager(kodi_movies=[self._kodi_movie()], simkl_movies=[self._movie(stamp)])
            mgr.import_movies_from_simkl()
            for write in _watch_writes(mgr, "VideoLibrary.SetMovieDetails"):
                self.assertEqual(stamp is not None, "lastplayed" in write, (stamp, write))

    def test_an_already_watched_movie_is_not_touched(self):
        kodi = dict(self._kodi_movie(), playcount=1)
        mgr = _manager(kodi_movies=[kodi], simkl_movies=[self._movie(SIMKL_TIME)])
        mgr.import_movies_from_simkl()
        self.assertEqual([], mgr.writes)


class TestEpisodeImport(unittest.TestCase):

    def _show(self, episodes, status="watching"):
        return {"status": status,
                "show": {"title": "Snatch", "year": 2020, "ids": {"tvdb": "555"}},
                "seasons": [{"number": 8, "episodes": episodes}]}

    def _kodi(self):
        shows = {1: {"tvshowid": 1, "title": "Snatch", "year": 2020, "uniqueid": {"tvdb": "555"}}}
        episodes = [
            {"episodeid": 31, "tvshowid": 1, "season": 8, "episode": 3, "playcount": 0},
            {"episodeid": 32, "tvshowid": 1, "season": 8, "episode": 4, "playcount": 0},
        ]
        return shows, episodes

    def test_each_episode_gets_its_own_real_time(self):
        shows, episodes = self._kodi()
        simkl = self._show([{"number": 3, "watched_at": "2026-10-13T19:33:17Z"},
                            {"number": 4, "watched_at": "2026-10-14T02:01:40Z"}])
        mgr = _manager(kodi_shows=shows, kodi_episodes=episodes, simkl_watching=[simkl])
        mgr.import_episodes_from_simkl()
        by_id = {w["episodeid"]: w for w in _watch_writes(mgr, "VideoLibrary.SetEpisodeDetails")}
        self.assertEqual(_utc_iso_to_kodi_time("2026-10-13T19:33:17Z"), by_id[31]["lastplayed"])
        self.assertEqual(_utc_iso_to_kodi_time("2026-10-14T02:01:40Z"), by_id[32]["lastplayed"])

    def test_an_episode_with_no_real_time_is_marked_watched_with_nothing_invented(self):
        shows, episodes = self._kodi()
        simkl = self._show([{"number": 3}, {"number": 4, "watched_at": "2026-10-14T02:01:40Z"}])
        mgr = _manager(kodi_shows=shows, kodi_episodes=episodes, simkl_watching=[simkl])
        mgr.import_episodes_from_simkl()
        by_id = {w["episodeid"]: w for w in _watch_writes(mgr, "VideoLibrary.SetEpisodeDetails")}
        self.assertEqual({"episodeid": 31, "playcount": 1}, by_id[31])
        self.assertIn("lastplayed", by_id[32])

    def test_the_episode_fetches_ask_simkl_for_per_episode_times(self):
        shows, episodes = self._kodi()
        mgr = _manager(kodi_shows=shows, kodi_episodes=episodes,
                       simkl_watching=[self._show([{"number": 3}])])
        mgr.import_episodes_from_simkl()
        shows_calls = [c for c in mgr.api.get_all_items.call_args_list if c.args[0] == "shows"]
        self.assertEqual(2, len(shows_calls))
        for call in shows_calls:
            self.assertTrue(call.kwargs.get("extended"))
            self.assertTrue(call.kwargs.get("episode_watched_at"))

    def test_marking_a_whole_completed_show_invents_no_times(self):
        shows, episodes = self._kodi()
        mgr = _manager(kodi_shows=shows, kodi_episodes=episodes)
        mgr._mark_all_episodes_watched(1, "Snatch", {8: {3: episodes[0], 4: episodes[1]}})
        writes = _watch_writes(mgr, "VideoLibrary.SetEpisodeDetails")
        self.assertEqual(2, len(writes))
        for write in writes:
            self.assertNotIn("lastplayed", write)


class TestImportIsAMerge(unittest.TestCase):
    """The import only ever ADDS watched state. SIMKL not listing an item is not evidence it was never watched, and
    an item Kodi already has watched keeps its own play count and last-played (2026-10-17: a full sync unmarked
    eight watched movies that SIMKL did not list)."""

    def setUp(self):
        # Even with every setting reading as enabled, including the retired 'unmark_not_on_simkl'.
        patcher = patch.object(sync_mod, "get_setting_bool", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_watched_movie_simkl_does_not_list_is_left_alone(self):
        kodi = {"movieid": 9, "title": "Derelict", "year": 2024, "playcount": 1,
                "lastplayed": "2025-07-22 21:08:41", "uniqueid": {"imdb": "tt0000009"}}
        other = {"movie": {"title": "Something Else", "year": 2000, "ids": {"imdb": "tt0000001"}},
                 "last_watched_at": SIMKL_TIME}
        mgr = _manager(kodi_movies=[kodi], simkl_movies=[other])
        mgr.import_movies_from_simkl()          # a FULL import: no date_from
        self.assertEqual([], mgr.writes)

    def test_a_watched_episode_simkl_does_not_list_is_left_alone(self):
        shows = {1: {"tvshowid": 1, "title": "Snatch", "year": 2020, "uniqueid": {"tvdb": "555"}}}
        episodes = [{"episodeid": 31, "tvshowid": 1, "season": 8, "episode": 3, "playcount": 1}]
        simkl = {"status": "watching", "show": {"title": "Snatch", "year": 2020, "ids": {"tvdb": "555"}},
                 "seasons": [{"number": 8, "episodes": [{"number": 9, "watched_at": SIMKL_TIME}]}]}
        mgr = _manager(kodi_shows=shows, kodi_episodes=episodes, simkl_watching=[simkl])
        mgr.import_episodes_from_simkl()
        self.assertEqual([], mgr.writes)

    def test_an_item_kodi_already_has_watched_is_not_rewritten(self):
        kodi = {"movieid": 7, "title": "Onslaught", "year": 2026, "playcount": 1,
                "lastplayed": "2026-10-10 22:58:07", "uniqueid": {"imdb": "tt32268691"}}
        item = {"movie": {"title": "Onslaught", "year": 2026, "ids": {"imdb": "tt32268691"}},
                "last_watched_at": SIMKL_TIME}
        mgr = _manager(kodi_movies=[kodi], simkl_movies=[item])
        mgr.import_movies_from_simkl()
        self.assertEqual([], mgr.writes)

    def test_no_unmark_helpers_remain(self):
        for name in ("_unmark_movies_not_on_simkl", "_unmark_episodes_not_on_simkl"):
            self.assertFalse(hasattr(SyncManager, name), name)


class TestApiRequestsEpisodeTimes(unittest.TestCase):

    def _api(self):
        api = object.__new__(SimklAPI)
        api._request = MagicMock(return_value=[])
        return api

    def test_episode_watched_at_is_sent_with_extended(self):
        api = self._api()
        api.get_all_items("shows", "watching", extended=True, episode_watched_at=True)
        params = api._request.call_args.kwargs["params"]
        self.assertEqual("full", params["extended"])
        self.assertEqual("yes", params["episode_watched_at"])

    def test_not_sent_by_default_or_without_extended(self):
        api = self._api()
        api.get_all_items("shows", "watching", extended=True)
        self.assertNotIn("episode_watched_at", api._request.call_args.kwargs["params"])
        api.get_all_items("shows", "watching", episode_watched_at=True)
        self.assertIsNone(api._request.call_args.kwargs.get("params"))


if __name__ == "__main__":
    unittest.main()
