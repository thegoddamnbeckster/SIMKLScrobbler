# -*- coding: utf-8 -*-
"""
A watch must reach SIMKL's history, or be kept until it does (2026-10-17).

Spider-Man: Brand New Day was rewatched on the upstairs Shield and never appeared on SIMKL (the entry still showed
only the first watch). The history-recording path trusted too much:

* A /sync/history response is HTTP 200 with {"added": {...}, "not_found": {...}} whether or not anything was
  recorded, but the scrobbler treated any non-empty dict as success ("Rewatch history entry added successfully"),
  so "SIMKL could not match it" and "SIMKL added nothing" both read as done.
* The fallback used when /scrobble/stop failed sent no watched_at, even for a rewatch - and without a time SIMKL
  treats an already-watched item as already recorded and adds nothing.
* A request that got no answer was logged and forgotten; nothing retried it and a later sync could not rebuild it.
* The movie export advanced its sync state even after a failed batch, so that batch was never retried either.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 -- side-effect: stubs xbmc & friends

from resources.lib import pending_history as pending_mod
from resources.lib import scrobbler as scrobbler_mod
from resources.lib.api import summarize_history_result
from resources.lib.pending_history import PendingHistory, MAX_ENTRIES, MAX_NOT_FOUND_STRIKES
from resources.lib.scrobbler import SimklScrobbler
from resources.lib.sync import SyncManager

ADDED_ONE = {"added": {"movies": 1, "shows": 0, "episodes": 0, "statuses": []},
             "not_found": {"movies": [], "shows": [], "episodes": []}}
ADDED_NONE = {"added": {"movies": 0, "shows": 0, "episodes": 0, "statuses": []},
              "not_found": {"movies": [], "shows": [], "episodes": []}}
NOT_FOUND = {"added": {"movies": 0, "shows": 0, "episodes": 0, "statuses": []},
             "not_found": {"movies": [{"title": "x"}], "shows": [], "episodes": []}}
STOP_OK = {"id": 123, "action": "scrobble"}


class MemoryQueue(PendingHistory):
    """A PendingHistory that keeps its entries in memory."""
    def __init__(self):
        self.data = []

    def _load(self):
        return json.loads(json.dumps(self.data))

    def _save(self, entries):
        self.data = json.loads(json.dumps(entries))


class TestSummarizeHistoryResult(unittest.TestCase):

    def test_no_response_is_none(self):
        self.assertIsNone(summarize_history_result(None))
        self.assertIsNone(summarize_history_result("oops"))

    def test_counts_added_movies_and_episodes(self):
        self.assertEqual((1, 0), summarize_history_result(ADDED_ONE))
        both = {"added": {"movies": 2, "episodes": 3}, "not_found": {}}
        self.assertEqual((5, 0), summarize_history_result(both))

    def test_added_nothing_is_not_the_same_as_not_found(self):
        self.assertEqual((0, 0), summarize_history_result(ADDED_NONE))
        self.assertEqual((0, 1), summarize_history_result(NOT_FOUND))

    def test_an_unexpected_shape_does_not_raise(self):
        self.assertEqual((0, 0), summarize_history_result({}))
        self.assertEqual((0, 0), summarize_history_result({"added": None, "not_found": None}))
        self.assertEqual((0, 0), summarize_history_result({"conflict": True}))


class TestPlaybackEndedRecordsHistory(unittest.TestCase):

    def setUp(self):
        self.queue = MemoryQueue()
        for target, value in (
            (scrobbler_mod, ("get_setting_int", lambda name, default=0: default)),
            (scrobbler_mod, ("get_setting_bool", lambda name, default=False: False)),
            (scrobbler_mod, ("PendingHistory", lambda: self.queue)),
        ):
            patcher = patch.object(target, value[0], value[1])
            patcher.start()
            self.addCleanup(patcher.stop)

    def _scrobbler(self, previously_watched, stop_response, history_result, media="movie"):
        api = MagicMock()
        api.add_to_history.return_value = history_result
        sc = SimklScrobbler(api)
        sc.is_playing = True
        sc.video_duration = 1000
        sc.watched_time = 990                       # 99%
        sc._was_previously_watched = previously_watched
        sc._scrobble = MagicMock(return_value=stop_response)
        sc._check_rating = MagicMock()
        if media == "movie":
            sc.current_video = {"type": "movie", "title": "Brand New Day", "dbid": 5}
            sc.current_video_info = {"title": "Brand New Day", "year": 2026,
                                     "ids": {"imdb": "tt22084616", "tmdb": 969681}}
        else:
            sc.current_video = {"type": "episode", "title": "Pilot", "dbid": 6}
            sc.current_video_info = {"show": {"title": "Snatch", "ids": {"tvdb": 555}},
                                     "episode": {"season": 8, "number": 3}}
        return sc

    @staticmethod
    def _sent_movie(sc):
        return sc.api.add_to_history.call_args.kwargs["movies"][0]

    def test_a_rewatch_is_recorded_with_its_finish_time(self):
        sc = self._scrobbler(True, STOP_OK, ADDED_ONE)
        sc.playback_ended()
        sc.api.add_to_history.assert_called_once()
        self.assertRegex(self._sent_movie(sc)["watched_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual([], self.queue.data)

    def test_a_first_watch_that_scrobble_stop_recorded_adds_no_history(self):
        sc = self._scrobbler(False, STOP_OK, ADDED_ONE)
        sc.playback_ended()
        sc.api.add_to_history.assert_not_called()

    def test_a_rewatch_simkl_cannot_match_is_not_reported_as_recorded_or_queued(self):
        sc = self._scrobbler(True, STOP_OK, NOT_FOUND)
        sc.playback_ended()
        sc.api.add_to_history.assert_called_once()
        self.assertEqual([], self.queue.data)     # retrying cannot help an item SIMKL does not know

    def test_a_rewatch_whose_request_got_no_answer_is_queued_with_its_finish_time(self):
        sc = self._scrobbler(True, STOP_OK, None)
        sc.playback_ended()
        self.assertEqual(1, len(self.queue.data))
        entry = self.queue.data[0]
        self.assertEqual("movie", entry["kind"])
        self.assertEqual(self._sent_movie(sc)["watched_at"], entry["payload"]["watched_at"])

    def test_a_rewatch_simkl_adds_nothing_for_is_not_reported_as_recorded(self):
        # added=0 on a rewatch means the stop already recorded it, it was within 2 days of the last watch, or the
        # account is not Pro/VIP - never "SIMKL recorded it".
        sc = self._scrobbler(True, STOP_OK, ADDED_NONE)
        with patch.object(scrobbler_mod, "log") as log_mock, patch.object(scrobbler_mod, "log_warning") as warn_mock:
            sc.playback_ended()
        text = " ".join(str(c.args[0]) for c in log_mock.call_args_list + warn_mock.call_args_list)
        self.assertIn("SIMKL added no new rewatch", text)
        self.assertNotIn("SIMKL recorded Brand New Day", text)

    def test_the_rewatch_history_call_carries_allow_rewatch(self):
        # Without ?allow_rewatch=yes SIMKL answers 200 and adds nothing for an already-watched item (verified live
        # 2026-10-18; the add-on never sent it before 7.9.26, so no rewatch ever reached SIMKL).
        sc = self._scrobbler(True, STOP_OK, ADDED_ONE)
        sc.playback_ended()
        self.assertIs(True, sc.api.add_to_history.call_args.kwargs["allow_rewatch"])

    def test_a_first_watch_fallback_does_not_ask_for_a_rewatch(self):
        sc = self._scrobbler(False, None, ADDED_ONE)
        sc.playback_ended()
        self.assertIs(False, sc.api.add_to_history.call_args.kwargs["allow_rewatch"])

    def test_a_queued_rewatch_is_retried_as_a_rewatch(self):
        sc = self._scrobbler(True, STOP_OK, None)
        sc.playback_ended()
        self.assertIs(True, self.queue.data[0]["rewatch"])
        api = MagicMock()
        api.add_to_history.return_value = ADDED_ONE
        self.queue.flush(api)
        self.assertIs(True, api.add_to_history.call_args.kwargs["allow_rewatch"])

    def test_the_stop_is_sent_asking_simkl_to_judge_a_rewatch_but_start_is_not(self):
        api = MagicMock()
        sc = SimklScrobbler(api)
        sc.current_video = {"type": "movie", "title": "Brand New Day"}
        sc.current_video_info = {"title": "Brand New Day", "ids": {"imdb": "tt22084616"}}
        sc.video_duration, sc.watched_time = 1000, 990
        sc._scrobble("stop")
        self.assertIs(True, api.scrobble.call_args.kwargs["allow_rewatch"])
        sc._scrobble("start")
        self.assertIs(False, api.scrobble.call_args.kwargs["allow_rewatch"])

    def test_the_fallback_for_a_rewatch_carries_the_time_too(self):
        # /scrobble/stop failed (None) on a rewatch: the fallback used to send no watched_at, so SIMKL added nothing.
        sc = self._scrobbler(True, None, ADDED_ONE)
        sc.playback_ended()
        self.assertIn("watched_at", self._sent_movie(sc))
        sc._check_rating.assert_called_once()

    def test_the_fallback_for_a_first_watch_also_carries_the_time_so_a_retry_cannot_double_record(self):
        # The API layer retries a timed-out POST. A retry carrying "now" again could record the watch twice if the
        # first request had landed; an explicit time makes it the same entry.
        sc = self._scrobbler(False, None, None)
        sc.playback_ended()
        sent_time = self._sent_movie(sc)["watched_at"]
        self.assertEqual(1, len(self.queue.data))
        self.assertEqual(sent_time, self.queue.data[0]["payload"]["watched_at"])

    def test_the_fallback_counts_a_zero_added_response_as_recorded(self):
        # SIMKL already had exactly this entry: nothing to retry, nothing to warn the user about.
        sc = self._scrobbler(False, None, ADDED_NONE)
        sc.playback_ended()
        sc._check_rating.assert_called_once()
        self.assertEqual([], self.queue.data)

    def test_the_fallback_is_not_a_success_when_simkl_cannot_match_the_item(self):
        sc = self._scrobbler(False, None, NOT_FOUND)
        sc.playback_ended()
        sc._check_rating.assert_not_called()

    def test_an_episode_rewatch_is_recorded_and_queued_as_a_show_entry(self):
        sc = self._scrobbler(True, STOP_OK, None, media="episode")
        sc.playback_ended()
        sent = sc.api.add_to_history.call_args.kwargs["shows"][0]
        episode = sent["seasons"][0]["episodes"][0]
        self.assertEqual(3, episode["number"])
        self.assertIn("watched_at", episode)
        self.assertEqual("show", self.queue.data[0]["kind"])


class TestPendingHistoryQueue(unittest.TestCase):

    def _api(self, *results):
        api = MagicMock()
        api.add_to_history.side_effect = list(results)
        return api

    def _queue_one(self, queue):
        queue.add("movie", {"title": "Brand New Day", "ids": {"imdb": "tt22084616"},
                            "watched_at": "2026-10-15T03:21:56Z"}, label="Brand New Day")

    def test_a_recorded_entry_leaves_the_queue(self):
        queue = MemoryQueue()
        self._queue_one(queue)
        self.assertEqual((1, 0), queue.flush(self._api(ADDED_ONE)))
        self.assertEqual([], queue.data)

    def test_the_retry_keeps_the_original_watch_time(self):
        queue = MemoryQueue()
        self._queue_one(queue)
        api = self._api(ADDED_ONE)
        queue.flush(api)
        self.assertEqual("2026-10-15T03:21:56Z", api.add_to_history.call_args.kwargs["movies"][0]["watched_at"])

    def test_an_entry_simkl_already_has_is_done(self):
        queue = MemoryQueue()
        self._queue_one(queue)
        self.assertEqual((1, 0), queue.flush(self._api(ADDED_NONE)))

    def test_a_request_with_no_answer_stays_for_next_time_without_a_strike(self):
        queue = MemoryQueue()
        self._queue_one(queue)
        for _ in range(MAX_NOT_FOUND_STRIKES + 2):
            self.assertEqual((0, 1), queue.flush(self._api(None)))
        self.assertEqual(1, len(queue.data))
        self.assertEqual(0, queue.data[0]["strikes"])

    def test_an_item_simkl_cannot_match_is_dropped_after_a_few_tries(self):
        queue = MemoryQueue()
        self._queue_one(queue)
        for attempt in range(1, MAX_NOT_FOUND_STRIKES):
            self.assertEqual((0, 1), queue.flush(self._api(NOT_FOUND)))
            self.assertEqual(attempt, queue.data[0]["strikes"])
        self.assertEqual((0, 0), queue.flush(self._api(NOT_FOUND)))
        self.assertEqual([], queue.data)

    def test_the_queue_is_capped_dropping_the_oldest(self):
        queue = MemoryQueue()
        for i in range(MAX_ENTRIES + 5):
            queue.add("movie", {"title": f"m{i}"}, label=f"m{i}")
        self.assertEqual(MAX_ENTRIES, len(queue.data))
        self.assertEqual("m5", queue.data[0]["label"])

    def test_flushing_an_empty_queue_makes_no_requests(self):
        api = MagicMock()
        self.assertEqual((0, 0), MemoryQueue().flush(api))
        api.add_to_history.assert_not_called()


class TestMovieExportRetriesAfterAFailedBatch(unittest.TestCase):

    def _manager(self, history_result):
        mgr = object.__new__(SyncManager)
        mgr.stats = {"errors": 0, "movies_exported": 0}
        mgr.force_full_sync = True
        mgr.api = MagicMock()
        mgr.api.add_to_history.return_value = history_result
        mgr.get_kodi_movies = lambda: [{"title": "Brand New Day", "year": 2026, "playcount": 2,
                                        "lastplayed": "2026-10-14 21:21:56",
                                        "uniqueid": {"imdb": "tt22084616"}}]
        mgr._save_sync_state = MagicMock()
        return mgr

    def test_a_failed_batch_does_not_advance_the_sync_state(self):
        mgr = self._manager(None)
        mgr.export_movies_to_simkl()
        mgr._save_sync_state.assert_not_called()
        self.assertEqual(1, mgr.stats["errors"])

    def test_a_successful_export_advances_the_sync_state(self):
        mgr = self._manager(ADDED_ONE)
        mgr.export_movies_to_simkl()
        mgr._save_sync_state.assert_called_once()
        self.assertEqual("movies", mgr._save_sync_state.call_args.args[0])


class TestRewatchParameterReachesSimkl(unittest.TestCase):
    """allow_rewatch is read from the query string. The API layer used to drop the params of every POST, so it could
    never have reached SIMKL even where it was meant to."""

    def _api(self):
        from resources.lib.api import SimklAPI
        api = object.__new__(SimklAPI)
        api._request = MagicMock(return_value={})
        return api

    def test_a_post_sends_its_params(self):
        from resources.lib.api import SimklAPI
        api = object.__new__(SimklAPI)
        api.session = MagicMock()
        api._execute_request("POST", "https://api.simkl.com/sync/history", {"movies": []}, {"allow_rewatch": "yes"}, 30)
        self.assertEqual({"allow_rewatch": "yes"}, api.session.post.call_args.kwargs["params"])

    def test_history_asks_for_a_rewatch_only_when_told_to(self):
        api = self._api()
        api.add_to_history(movies=[{"title": "x"}], allow_rewatch=True)
        self.assertEqual({"allow_rewatch": "yes"}, api._request.call_args.kwargs["params"])
        api.add_to_history(movies=[{"title": "x"}])
        self.assertIsNone(api._request.call_args.kwargs["params"])

    def test_scrobble_sends_allow_rewatch_on_stop_only(self):
        api = self._api()
        movie = {"title": "x", "ids": {"imdb": "tt1"}}
        api.scrobble("stop", movie=movie, progress=99, allow_rewatch=True)
        self.assertEqual({"allow_rewatch": "yes"}, api._request.call_args.kwargs["params"])
        api.scrobble("start", movie=movie, progress=1, allow_rewatch=True)
        self.assertIsNone(api._request.call_args.kwargs["params"])
        api.scrobble("stop", movie=movie, progress=99)
        self.assertIsNone(api._request.call_args.kwargs["params"])


if __name__ == "__main__":
    unittest.main()
