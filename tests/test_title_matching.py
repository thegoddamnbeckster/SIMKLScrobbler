# -*- coding: utf-8 -*-
"""
Regression tests for two related bugs found while investigating a report that
rewatching "Marvel's What If...?" reset previously-watched episodes back to
unwatched in Kodi:

1. normalize_title() — TheTVDB/Kodi scrapers commonly store the show title
   with a single Unicode ellipsis character ("What If…?"), while SIMKL
   stores it as three ASCII periods ("What If...?"). A plain .lower().strip()
   treats these as different strings, so the title+year fallback used by
   SyncManager silently fails to match the show.

2. _build_simkl_episode_set() / _unmark_episodes_not_on_simkl() — when a show
   fails to match (for ANY reason: the ellipsis mismatch above, a missing ID,
   etc.), the old code treated "couldn't match" the same as "confirmed absent
   from SIMKL" and unmarked every watched episode of that show in Kodi. The
   fix tracks which shows were actually matched and leaves unmatched shows
   untouched instead of wiping their watch history.

3. find_imdb_id_in_uniqueid() — some Kodi scraper configs store an IMDb id
   under a generic "unknown" uniqueid key instead of tagging it "imdb", which
   made a correctly-scraped show look like it had no IDs at all and forced it
   onto the same fragile title+year path. An IMDb id's "tt\\d+" format is
   unambiguous regardless of key name, so it can be rescued safely.

4. has_suspicious_tvdb_tmdb_collision() — confirmed live via a real kodi.log:
   Kodi's scraper attached the identical value to both tvdb_id and tmdb_id
   for "What If...?" (both "10771832"). TVDB and TMDB are independent
   numbering spaces, so a genuine collision is effectively impossible —
   this meant the scraper wrote one (wrong) value into both fields. Trusting
   it sent a bogus id to SIMKL's real-time scrobble endpoints, which
   intermittently matched or 404'd depending on how SIMKL resolved it,
   while the separate bulk-sync path fell back to title+year matching. That
   split-brain behavior caused a genuinely-watched episode's per-episode
   status to disagree between the two paths, and the "unmark not on SIMKL"
   pass treated the disagreement as "not watched" and reset it in Kodi.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.kodi_stubs  # noqa: F401 — side-effect: stubs xbmc/xbmcaddon/xbmcgui

from resources.lib.sync import SyncManager
from resources.lib.utils import (
    normalize_title, find_imdb_id_in_uniqueid, has_suspicious_tvdb_tmdb_collision,
)


# ---------------------------------------------------------------------------
# normalize_title()
# ---------------------------------------------------------------------------

class TestNormalizeTitle(unittest.TestCase):

    def test_unicode_ellipsis_matches_ascii_dots(self):
        """'What If…?' (single ellipsis char) and 'What If...?' (three
        periods) must normalize to the same key."""
        self.assertEqual(normalize_title("What If…?"), normalize_title("What If...?"))

    def test_curly_apostrophe_matches_straight(self):
        self.assertEqual(normalize_title("Marvel’s"), normalize_title("Marvel's"))

    def test_case_and_whitespace_still_normalized(self):
        self.assertEqual(normalize_title("  What If...?  "), "what if...?")

    def test_empty_and_none_return_empty_string(self):
        self.assertEqual(normalize_title(""), "")
        self.assertEqual(normalize_title(None), "")

    def test_non_breaking_space_matches_regular_space(self):
        """NFKC folds U+00A0 (non-breaking space) to a regular space."""
        title_with_nbsp = "What" + chr(0x00A0) + "If...?"
        self.assertEqual(normalize_title(title_with_nbsp), normalize_title("What If...?"))

    def test_collapses_double_spaces(self):
        self.assertEqual(normalize_title("What   If...?"), "what if...?")

    def test_does_not_falsely_equate_different_titles(self):
        """Sanity guard: normalization must not be so aggressive that two
        genuinely different titles collapse to the same key."""
        self.assertNotEqual(normalize_title("What If...?"), normalize_title("What Now?"))


# ---------------------------------------------------------------------------
# find_imdb_id_in_uniqueid() — rescue from a mislabeled uniqueid key
# ---------------------------------------------------------------------------

class TestFindImdbIdInUniqueid(unittest.TestCase):

    def test_finds_imdb_id_under_unknown_key(self):
        self.assertEqual(
            find_imdb_id_in_uniqueid({"unknown": "tt2461178"}),
            "tt2461178",
        )

    def test_returns_none_when_no_tt_pattern_present(self):
        """A bare numeric value under 'unknown' could be a tvdb/tmdb id —
        must NOT be guessed as imdb since the format is ambiguous."""
        self.assertIsNone(find_imdb_id_in_uniqueid({"unknown": "81189"}))

    def test_empty_or_none_uniqueid_returns_none(self):
        self.assertIsNone(find_imdb_id_in_uniqueid({}))
        self.assertIsNone(find_imdb_id_in_uniqueid(None))

    def test_prefers_properly_tagged_imdb_key_in_extract_ids(self):
        """Integration check: _extract_ids() must still use the properly
        tagged 'imdb' key when present, not need the rescue path at all."""
        with patch('resources.lib.sync.SimklAPI'):
            manager = SyncManager(show_progress=False, silent=True)
        ids = manager._extract_ids({"uniqueid": {"imdb": "tt0782533", "unknown": "999"}})
        self.assertEqual(ids["imdb"], "tt0782533")

    def test_extract_ids_rescues_imdb_from_unknown_key(self):
        """Integration check: a show whose scraper only set 'unknown' must
        still yield a usable imdb id via _extract_ids()."""
        with patch('resources.lib.sync.SimklAPI'):
            manager = SyncManager(show_progress=False, silent=True)
        ids = manager._extract_ids({"uniqueid": {"unknown": "tt2461178"}})
        self.assertEqual(ids["imdb"], "tt2461178")


# ---------------------------------------------------------------------------
# has_suspicious_tvdb_tmdb_collision() — the "What If...?" root cause
# ---------------------------------------------------------------------------

class TestTvdbTmdbCollisionGuard(unittest.TestCase):

    def test_identical_values_are_suspicious(self):
        """Reproduces the exact case from the live kodi.log: both ids '10771832'."""
        self.assertTrue(has_suspicious_tvdb_tmdb_collision("10771832", "10771832"))

    def test_identical_across_str_and_int_types(self):
        self.assertTrue(has_suspicious_tvdb_tmdb_collision("10771832", 10771832))

    def test_different_values_not_suspicious(self):
        self.assertFalse(has_suspicious_tvdb_tmdb_collision("355243", "92749"))

    def test_missing_either_value_not_suspicious(self):
        self.assertFalse(has_suspicious_tvdb_tmdb_collision(None, "92749"))
        self.assertFalse(has_suspicious_tvdb_tmdb_collision("355243", None))
        self.assertFalse(has_suspicious_tvdb_tmdb_collision(None, None))

    def test_extract_ids_drops_both_on_collision(self):
        """Integration check: _extract_ids() must drop both ids rather than
        forwarding a bogus pair to SIMKL. With no other usable id, the
        method's contract is to return None (no ids)."""
        with patch('resources.lib.sync.SimklAPI'):
            manager = SyncManager(show_progress=False, silent=True)
        ids = manager._extract_ids({"uniqueid": {"tvdb": "10771832", "tmdb": "10771832"}})
        self.assertIsNone(ids)

    def test_extract_ids_keeps_distinct_ids(self):
        """Sanity check: the guard must not affect a normal, non-colliding pair."""
        with patch('resources.lib.sync.SimklAPI'):
            manager = SyncManager(show_progress=False, silent=True)
        ids = manager._extract_ids({"uniqueid": {"tvdb": "355243", "tmdb": "92749"}})
        self.assertEqual(ids["tvdb"], 355243)
        self.assertEqual(ids["tmdb"], 92749)

    def test_build_kodi_show_index_skips_colliding_ids(self):
        """Integration check: a show with colliding tvdb/tmdb must not be
        indexed under either id (would otherwise let a bogus id falsely
        match a SIMKL show and later trigger the destructive unmark path)."""
        with patch('resources.lib.sync.SimklAPI'):
            manager = SyncManager(show_progress=False, silent=True)
        kodi_show = {
            "tvshowid": 48, "title": "What If...?", "year": 2021,
            "uniqueid": {"tvdb": "10771832", "tmdb": "10771832"},
        }
        index = manager._build_kodi_show_index({48: kodi_show})
        self.assertEqual(index["tvdb"], {})
        self.assertEqual(index["tmdb"], {})
        # Title+year fallback must still work despite the dropped ids.
        self.assertIn(("what if...?", 2021), index["title_year"])


# ---------------------------------------------------------------------------
# _match_show_to_kodi() — title+year fallback with ellipsis mismatch
# ---------------------------------------------------------------------------

class TestMatchShowToKodiEllipsis(unittest.TestCase):

    def setUp(self):
        with patch('resources.lib.sync.SimklAPI'):
            self.manager = SyncManager(show_progress=False, silent=True)
        self.manager.api = MagicMock()

    def test_title_year_fallback_matches_despite_ellipsis_variant(self):
        """Kodi stores the show with a Unicode ellipsis; SIMKL returns three
        ASCII periods. No overlapping IDs — must still match via title+year."""
        kodi_show = {"tvshowid": 42, "title": "Marvel's What If…?", "year": 2021}
        kodi_index = self.manager._build_kodi_show_index({42: kodi_show})

        matched = self.manager._match_show_to_kodi(
            simkl_ids={},  # no overlapping IDs — forces title+year fallback
            kodi_shows_by_id=kodi_index,
            show_title="Marvel's What If...?",
            show_year=2021,
        )
        self.assertIsNotNone(matched)
        self.assertEqual(matched["tvshowid"], 42)


# ---------------------------------------------------------------------------
# _build_simkl_episode_set() / _unmark_episodes_not_on_simkl() — safety net
# ---------------------------------------------------------------------------

class TestUnmarkSafetyNet(unittest.TestCase):
    """
    A show that fails to match a SIMKL entry must be left alone by the unmark
    pass, not have its watched episodes wiped.
    """

    def setUp(self):
        with patch('resources.lib.sync.SimklAPI'):
            self.manager = SyncManager(show_progress=False, silent=True)
        self.manager.api = MagicMock()
        self.manager._set_episode_playcount = MagicMock(return_value=True)

    def test_unmatched_show_excluded_from_matched_set(self):
        """A SIMKL show with no ID overlap and a title that doesn't match any
        Kodi show must not appear in matched_tvshowids."""
        kodi_index = self.manager._build_kodi_show_index({
            1: {"tvshowid": 1, "title": "ReBoot", "year": 1994, "uniqueid": {}},
        })
        simkl_shows = [{
            "show": {"title": "What If...?", "year": 2021, "ids": {}},
            "seasons": [{"number": 1, "episodes": [{"number": 1}]}],
        }]

        episode_set, matched = self.manager._build_simkl_episode_set(simkl_shows, kodi_index)
        self.assertEqual(episode_set, set())
        self.assertEqual(matched, set())

    def test_unmark_leaves_unmatched_show_untouched(self):
        """Regression for the destructive bug: episodes of a show that failed
        to match must NOT be unmarked, even though they're absent from
        simkl_episodes (which is empty for an unmatched show)."""
        kodi_episodes = [
            {"tvshowid": 99, "season": 1, "episode": 1, "episodeid": 501,
             "playcount": 1, "showtitle": "What If...?"},
            {"tvshowid": 99, "season": 2, "episode": 1, "episodeid": 502,
             "playcount": 1, "showtitle": "What If...?"},
        ]
        simkl_episodes = set()       # nothing matched (show 99 failed to match)
        matched_tvshowids = set()    # show 99 is NOT in the matched set

        unmarked = self.manager._unmark_episodes_not_on_simkl(
            kodi_episodes, simkl_episodes, matched_tvshowids
        )

        self.assertEqual(unmarked, 0)
        self.manager._set_episode_playcount.assert_not_called()

    def test_unmark_still_fires_for_matched_show_missing_episode(self):
        """Sanity check the safety net doesn't disable the feature entirely:
        a genuinely-matched show with an episode absent from SIMKL must still
        be unmarked."""
        kodi_episodes = [
            {"tvshowid": 7, "season": 1, "episode": 1, "episodeid": 701,
             "playcount": 1, "showtitle": "ReBoot"},
        ]
        simkl_episodes = set()          # show 7 matched, but this episode isn't watched on SIMKL
        matched_tvshowids = {7}         # show 7 WAS successfully matched

        unmarked = self.manager._unmark_episodes_not_on_simkl(
            kodi_episodes, simkl_episodes, matched_tvshowids
        )

        self.assertEqual(unmarked, 1)
        self.manager._set_episode_playcount.assert_called_once_with(701, 0)


if __name__ == '__main__':
    unittest.main()
