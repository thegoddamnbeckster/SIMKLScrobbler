# -*- coding: utf-8 -*-
"""
Tests for SIMKL API ID building in scrobbler.py and sync.py.

Core invariants being tested:
  - tvdb and tmdb IDs sent to SIMKL must be integers (the API rejects strings)
  - imdb IDs must stay strings ("tt1234567" format is never numeric)
  - Bare numeric imdb strings (no "tt" prefix) are normalised to "tt{n}"
  - Malformed (non-numeric) tvdb/tmdb values are dropped, not forwarded
  - Malformed tvdb/tmdb values produce a log_warning (not silent failure)
  - Episode air year is never placed on the show object
  - search_tv() is called without year when episode IDs are absent
    (video_data["year"] is the episode's air year, not the show's premiere year)
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Kodi stubs must be installed before any addon module is imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 — side-effect: stubs xbmc/xbmcaddon/xbmcgui

from resources.lib.scrobbler import SimklScrobbler
from resources.lib.sync import SyncManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scrobbler():
    api = MagicMock()
    api.search_tv = MagicMock(return_value=[])
    api.search_movie = MagicMock(return_value=[])
    return SimklScrobbler(api)


def _episode_data(**overrides):
    base = {
        'type': 'episode',
        'show_title': 'ReBoot',
        'title': 'Mousetrap',
        'season': 3,
        'episode': 12,
        'year': 1997,           # episode air year — NOT show premiere year (1994)
        'imdb_id': 'tt0782533',
        'tvdb_id': '105448',    # Kodi delivers as string
        'tmdb_id': '147601',    # Kodi delivers as string
    }
    base.update(overrides)
    return base


def _movie_data(**overrides):
    base = {
        'type': 'movie',
        'title': 'Fight Club',
        'year': 1999,
        'imdb_id': 'tt0137523',
        'tmdb_id': '550',       # Kodi delivers as string
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _identify_episode() — ID types
# ---------------------------------------------------------------------------

class TestIdentifyEpisodeIdTypes(unittest.TestCase):

    def setUp(self):
        self.s = _make_scrobbler()

    def _ids(self, **overrides):
        result = self.s._identify_episode(_episode_data(**overrides))
        self.assertIsNotNone(result, "_identify_episode() returned None — check for an unhandled error")
        return result['show']['ids']

    def test_tvdb_is_integer(self):
        """tvdb must be int — SIMKL rejects strings."""
        ids = self._ids()
        self.assertIsInstance(ids['tvdb'], int)
        self.assertEqual(ids['tvdb'], 105448)

    def test_tmdb_is_integer(self):
        """tmdb must be int — SIMKL rejects strings."""
        ids = self._ids()
        self.assertIsInstance(ids['tmdb'], int)
        self.assertEqual(ids['tmdb'], 147601)

    def test_imdb_is_string(self):
        """imdb must stay a string; it has a 'tt' prefix, not a raw number."""
        ids = self._ids()
        self.assertIsInstance(ids['imdb'], str)
        self.assertEqual(ids['imdb'], 'tt0782533')

    def test_imdb_bare_numeric_gets_tt_prefix(self):
        """A bare numeric imdb string (no 'tt' prefix) must be normalised to 'tt{n}'."""
        ids = self._ids(imdb_id='0782533')
        self.assertEqual(ids['imdb'], 'tt0782533')

    def test_imdb_already_prefixed_unchanged(self):
        """An already-prefixed 'tt...' string must pass through unmodified."""
        ids = self._ids(imdb_id='tt0782533')
        self.assertEqual(ids['imdb'], 'tt0782533')

    def test_numeric_string_tvdb_still_correct_value(self):
        """A string like '0105448' must round-trip to int 105448 (leading zero stripped)."""
        ids = self._ids(tvdb_id='0105448')
        self.assertEqual(ids['tvdb'], 105448)

    def test_malformed_tvdb_is_dropped_not_forwarded(self):
        """A non-numeric tvdb_id must not appear in the payload at all."""
        ids = self._ids(tvdb_id='abc-broken', tmdb_id=None)
        self.assertNotIn('tvdb', ids)

    def test_malformed_tvdb_logs_warning(self):
        """A non-numeric tvdb_id must emit a log_warning so the failure is diagnosable."""
        with patch('resources.lib.scrobbler.log_warning') as mock_warn:
            self._ids(tvdb_id='abc-broken', tmdb_id=None)
            mock_warn.assert_called_once()
            self.assertIn('tvdb_id', mock_warn.call_args[0][0])

    def test_malformed_tmdb_is_dropped_not_forwarded(self):
        """A non-numeric tmdb_id must not appear in the payload at all."""
        ids = self._ids(tmdb_id='not-a-number', tvdb_id=None)
        self.assertNotIn('tmdb', ids)

    def test_malformed_tmdb_logs_warning(self):
        """A non-numeric tmdb_id must emit a log_warning so the failure is diagnosable."""
        with patch('resources.lib.scrobbler.log_warning') as mock_warn:
            self._ids(tmdb_id='not-a-number', tvdb_id=None)
            mock_warn.assert_called_once()
            self.assertIn('tmdb_id', mock_warn.call_args[0][0])

    def test_native_int_tvdb_accepted(self):
        """
        int() on a native int is a no-op — documents that the cast does not raise
        if the value is already an int.  Kodi currently always delivers IDs as
        strings, so this path is theoretical; kept as a regression guard.
        """
        ids = self._ids(tvdb_id=105448)
        self.assertIsInstance(ids['tvdb'], int)
        self.assertEqual(ids['tvdb'], 105448)


# ---------------------------------------------------------------------------
# _identify_episode() — year handling
# ---------------------------------------------------------------------------

class TestIdentifyEpisodeYear(unittest.TestCase):

    def setUp(self):
        self.s = _make_scrobbler()

    def test_episode_year_absent_from_show_when_ids_present(self):
        """
        video_data['year'] is the episode's air year, not the show's premiere year.
        It must never be placed on the show object when IDs are available, because
        SIMKL may use it for validation and reject the show if years don't match.
        """
        result = self.s._identify_episode(_episode_data())
        self.assertIsNotNone(result, "_identify_episode() returned None")
        self.assertNotIn('year', result['show'])

    def test_search_tv_called_without_year_when_no_ids(self):
        """
        When no external IDs are available we must call search_tv(title) with NO
        year argument.  video_data['year'] is the episode air year; passing it
        filters out shows that premiered in a different year.
        """
        self.s._identify_episode(_episode_data(
            imdb_id=None, tvdb_id=None, tmdb_id=None
        ))
        self.s.api.search_tv.assert_called_once_with('ReBoot')

    def test_search_tv_not_called_when_ids_present(self):
        """If IDs are available, no search API call should be made."""
        self.s._identify_episode(_episode_data())
        self.s.api.search_tv.assert_not_called()


# ---------------------------------------------------------------------------
# _identify_movie() — ID types
# ---------------------------------------------------------------------------

class TestIdentifyMovieIdTypes(unittest.TestCase):

    def setUp(self):
        self.s = _make_scrobbler()

    def _ids(self, **overrides):
        result = self.s._identify_movie(_movie_data(**overrides))
        self.assertIsNotNone(result, "_identify_movie() returned None — check for an unhandled error")
        return result['ids']

    def test_tmdb_is_integer(self):
        ids = self._ids()
        self.assertIsInstance(ids['tmdb'], int)
        self.assertEqual(ids['tmdb'], 550)

    def test_imdb_is_string(self):
        ids = self._ids()
        self.assertIsInstance(ids['imdb'], str)
        self.assertEqual(ids['imdb'], 'tt0137523')

    def test_imdb_bare_numeric_gets_tt_prefix(self):
        """A bare numeric imdb string (no 'tt' prefix) must be normalised to 'tt{n}'."""
        ids = self._ids(imdb_id='0137523')
        self.assertEqual(ids['imdb'], 'tt0137523')

    def test_malformed_tmdb_is_dropped(self):
        ids = self._ids(tmdb_id='not-a-number')
        self.assertNotIn('tmdb', ids)

    def test_malformed_tmdb_logs_warning(self):
        """A non-numeric tmdb_id must emit a log_warning so the failure is diagnosable."""
        with patch('resources.lib.scrobbler.log_warning') as mock_warn:
            self._ids(tmdb_id='not-a-number')
            mock_warn.assert_called_once()
            self.assertIn('tmdb_id', mock_warn.call_args[0][0])


# ---------------------------------------------------------------------------
# _build_rating_info() — ID types in merge path
# ---------------------------------------------------------------------------

class TestBuildRatingInfoIdTypes(unittest.TestCase):
    """
    _build_rating_info() merges IDs from current_video_info (already fixed) with
    raw player data (current_video).  The merge path must also cast to int and
    log a warning on failure.
    """

    def setUp(self):
        self.s = _make_scrobbler()

    def _setup_episode_state(self, tvdb_id='105448', tmdb_id='147601'):
        """Prime scrobbler state as if playback_started() ran for an episode."""
        self.s.current_video = {
            'type': 'episode',
            'title': 'Mousetrap',
            'show_title': 'ReBoot',
            'season': 3,
            'episode': 12,
            'imdb_id': 'tt0782533',
            'tvdb_id': tvdb_id,
            'tmdb_id': tmdb_id,
        }
        # Simulate a video_info where identification found no IDs (worst case):
        # _build_rating_info() falls into the merge path for all three IDs.
        self.s.current_video_info = {
            'show': {'title': 'ReBoot', 'ids': {}},
            'episode': {'season': 3, 'number': 12},
        }

    def test_episode_tvdb_is_integer_in_rating_info(self):
        self._setup_episode_state()
        info = self.s._build_rating_info()
        self.assertIsNotNone(info)
        self.assertIsInstance(info['ids']['tvdb'], int)
        self.assertEqual(info['ids']['tvdb'], 105448)

    def test_episode_tmdb_is_integer_in_rating_info(self):
        self._setup_episode_state()
        info = self.s._build_rating_info()
        self.assertIsNotNone(info)
        self.assertIsInstance(info['ids']['tmdb'], int)
        self.assertEqual(info['ids']['tmdb'], 147601)

    def test_malformed_tvdb_dropped_in_rating_info(self):
        self._setup_episode_state(tvdb_id='broken')
        info = self.s._build_rating_info()
        self.assertIsNotNone(info)
        self.assertNotIn('tvdb', info['ids'])

    def test_malformed_tvdb_logs_warning_in_rating_info(self):
        """Silent failure in rating path is diagnosed via log_warning."""
        self._setup_episode_state(tvdb_id='broken')
        with patch('resources.lib.scrobbler.log_warning') as mock_warn:
            self.s._build_rating_info()
            mock_warn.assert_called_once()
            self.assertIn('tvdb_id', mock_warn.call_args[0][0])

    def test_malformed_tmdb_logs_warning_in_rating_info(self):
        """Episode tmdb_id drop path also produces a log_warning."""
        self._setup_episode_state(tmdb_id='broken')
        with patch('resources.lib.scrobbler.log_warning') as mock_warn:
            self.s._build_rating_info()
            mock_warn.assert_called_once()
            self.assertIn('tmdb_id', mock_warn.call_args[0][0])

    def test_movie_tmdb_is_integer_in_rating_info(self):
        self.s.current_video = {
            'type': 'movie', 'title': 'Fight Club', 'year': 1999,
            'imdb_id': 'tt0137523', 'tmdb_id': '550',
        }
        self.s.current_video_info = {'title': 'Fight Club', 'year': 1999, 'ids': {}}
        info = self.s._build_rating_info()
        self.assertIsNotNone(info)
        self.assertIsInstance(info['ids']['tmdb'], int)
        self.assertEqual(info['ids']['tmdb'], 550)

    def test_malformed_movie_tmdb_logs_warning_in_rating_info(self):
        """Movie tmdb_id drop path also produces a log_warning."""
        self.s.current_video = {
            'type': 'movie', 'title': 'Fight Club', 'year': 1999,
            'imdb_id': 'tt0137523', 'tmdb_id': 'broken',
        }
        self.s.current_video_info = {'title': 'Fight Club', 'year': 1999, 'ids': {}}
        with patch('resources.lib.scrobbler.log_warning') as mock_warn:
            self.s._build_rating_info()
            mock_warn.assert_called_once()
            self.assertIn('tmdb_id', mock_warn.call_args[0][0])


# ---------------------------------------------------------------------------
# SyncManager._extract_ids() — ID types
# ---------------------------------------------------------------------------

class TestExtractIds(unittest.TestCase):

    def setUp(self):
        # Patch SimklAPI so __init__ doesn't make real network calls, then call
        # __init__ normally so all instance attributes are correctly initialised.
        # This is safer than __new__() + manual attribute injection, which silently
        # breaks when __init__ adds new attributes that _extract_ids() later reads.
        with patch('resources.lib.sync.SimklAPI'):
            self.manager = SyncManager(show_progress=False, silent=True)
        # Provide a clean api mock (not needed by _extract_ids, but kept for clarity)
        self.manager.api = MagicMock()

    def _extract(self, item):
        return self.manager._extract_ids(item)

    def test_tvdb_from_uniqueid_is_integer(self):
        item = {'uniqueid': {'tvdb': '81189', 'imdb': 'tt0903747'}}
        ids = self._extract(item)
        self.assertIsInstance(ids['tvdb'], int)
        self.assertEqual(ids['tvdb'], 81189)

    def test_tmdb_from_uniqueid_is_integer(self):
        item = {'uniqueid': {'tmdb': '1396', 'imdb': 'tt0903747'}}
        ids = self._extract(item)
        self.assertIsInstance(ids['tmdb'], int)
        self.assertEqual(ids['tmdb'], 1396)

    def test_imdb_from_uniqueid_stays_string(self):
        item = {'uniqueid': {'imdb': 'tt0903747'}}
        ids = self._extract(item)
        self.assertIsInstance(ids['imdb'], str)
        self.assertEqual(ids['imdb'], 'tt0903747')

    def test_tvdb_from_imdbnumber_fallback_is_integer(self):
        """Older scrapers store a numeric TVDB ID in imdbnumber; must be int."""
        item = {'imdbnumber': '81189'}
        ids = self._extract(item)
        self.assertIsInstance(ids['tvdb'], int)
        self.assertEqual(ids['tvdb'], 81189)

    def test_imdb_from_imdbnumber_tt_prefix_stays_string(self):
        """imdbnumber starting with 'tt' is an IMDb ID — must stay a string."""
        item = {'imdbnumber': 'tt0903747'}
        ids = self._extract(item)
        self.assertIsInstance(ids['imdb'], str)
        self.assertEqual(ids['imdb'], 'tt0903747')

    def test_malformed_tvdb_from_uniqueid_is_dropped(self):
        item = {'uniqueid': {'tvdb': 'not-a-number'}}
        ids = self._extract(item)
        self.assertIsNone(ids)  # nothing valid → returns None

    def test_malformed_tvdb_logs_warning(self):
        """Malformed tvdb must emit a log_warning so sync failures are diagnosable."""
        with patch('resources.lib.sync.log_warning') as mock_warn:
            self._extract({'uniqueid': {'tvdb': 'not-a-number'}})
            mock_warn.assert_called_once()
            self.assertIn('tvdb', mock_warn.call_args[0][0])

    def test_malformed_tmdb_from_uniqueid_is_dropped(self):
        item = {'uniqueid': {'tmdb': 'broken'}}
        ids = self._extract(item)
        self.assertIsNone(ids)

    def test_malformed_tmdb_logs_warning(self):
        """Malformed tmdb must emit a log_warning so sync failures are diagnosable."""
        with patch('resources.lib.sync.log_warning') as mock_warn:
            self._extract({'uniqueid': {'tmdb': 'broken'}})
            mock_warn.assert_called_once()
            self.assertIn('tmdb', mock_warn.call_args[0][0])


if __name__ == '__main__':
    unittest.main()
