# -*- coding: utf-8 -*-
"""
SIMKL Sync Module
Version: 7.5.9
Last Modified: 2026-04-15

PHASE 9: Advanced Features & Polish

Handles synchronization between Kodi library and SIMKL.
Provides bidirectional sync of watch history with delta sync support.

Features:
- Export: Send Kodi watched items to SIMKL
- Import: Get SIMKL watched items and update Kodi
- Delta sync: Only sync items that changed since last sync
- Conflict resolution: User-configurable handling

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import json
import time
import xbmc
import xbmcaddon
import xbmcgui
from datetime import datetime, timezone
from resources.lib.utils import (
    log, log_error, log_debug, log_warning,
    get_setting_bool, notify
)
from resources.lib.api import SimklAPI

# Module version
__version__ = '7.8.9'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] sync.py v{__version__} - Sync manager module loading', level=xbmc.LOGINFO)


def _kodi_time_to_utc_iso(kodi_timestamp):
    """
    Convert Kodi's local time string to UTC ISO 8601 format.

    Kodi stores lastplayed as "YYYY-MM-DD HH:MM:SS" in local time.
    SIMKL expects ISO 8601 with "Z" suffix meaning UTC.

    Uses time.mktime() which correctly interprets the naive datetime as local
    time on all platforms, including Android where
    datetime.now(timezone.utc).astimezone().tzinfo returns None and crashes
    with "'NoneType' object is not callable".

    Args:
        kodi_timestamp: String like "2026-01-15 20:30:00"

    Returns:
        UTC ISO string like "2026-01-15T22:30:00Z" or None on failure
    """
    try:
        # Parse as local time (naive datetime)
        local_dt = datetime.strptime(kodi_timestamp, "%Y-%m-%d %H:%M:%S")
        # time.mktime() treats the timetuple as local time and returns a UTC POSIX
        # timestamp. datetime.fromtimestamp() then converts it to a UTC-aware
        # datetime. This portable approach works on all platforms including Android.
        posix_ts = time.mktime(local_dt.timetuple())
        utc_dt = datetime.fromtimestamp(posix_ts, tz=timezone.utc)
        return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as e:
        log_warning(f"[sync v{__version__}] _kodi_time_to_utc_iso() Failed to convert timestamp '{kodi_timestamp}': {e}")
        return None


class SyncManager:
    """
    Manages synchronization between Kodi and SIMKL.
    
    Think of this as a very organized, slightly OCD librarian who makes sure
    your Kodi library and SIMKL account are saying the same things.
    """
    
    def __init__(self, show_progress=False, silent=False, force_full_sync=False):
        """
        Initialize the sync manager.
        
        Args:
            show_progress (bool): Show progress dialog during sync
            silent (bool): Suppress notifications (for background sync)
            force_full_sync (bool): Skip delta detection, sync ALL watched items
        """
        # Create API with fresh token read to avoid stale cache on background threads
        try:
            fresh_addon = xbmcaddon.Addon('script.simkl.scrobbler')
            token = fresh_addon.getSetting('access_token')
        except Exception:
            token = None
        
        self.api = SimklAPI()
        # Override with fresh token if the default read got nothing
        if token and not self.api.access_token:
            self.api.access_token = token
            self.api.session.headers.update({
                "Authorization": f"Bearer {token}"
            })
            log(f"[sync v{__version__}] SyncManager.__init__() Injected fresh token into SyncManager API (len={len(token)})")
        
        self.show_progress = show_progress
        self.silent = silent
        self.force_full_sync = force_full_sync
        self.progress_dialog = None
        self.cancelled = False
        # Tracks which tvshowids have already triggered the "no external ID"
        # warning so _get_stable_show_key() doesn't emit N warnings for a show
        # with N episodes — one warning per show per sync run is enough.
        self._warned_tvshowids: set = set()
        
        # Stats for reporting
        self.stats = {
            'movies_exported': 0,
            'episodes_exported': 0,
            'shows_exported': 0,
            'movies_imported': 0,
            'episodes_imported': 0,
            'shows_imported': 0,
            'movies_unmarked': 0,
            'episodes_unmarked': 0,
            'ratings_exported': 0,
            'ratings_imported': 0,
            'errors': 0
        }
    
    def _notify(self, title, message):
        """Show notification unless silent mode is active."""
        if not self.silent:
            notify(title, message)
    
    def close(self):
        """Close the API session to free socket connections."""
        if self.api:
            self.api.close()
            log_debug(f"[sync v{__version__}] SyncManager.close() SyncManager API session closed")
    
    # ========== SIMKL Activity Tracking (Incremental Sync) ==========
    # Per SIMKL team feedback (Ennergizer, 2026-02-25): instead of fetching
    # ALL items from /sync/all-items/ on every sync, we should:
    #   1. Call /sync/activities to get timestamps of last changes
    #   2. Compare to stored timestamps from last successful sync
    #   3. Only call /sync/all-items/?date_from= when changes are detected
    # This dramatically reduces API payload size and server load.
    
    def _load_activity_timestamps(self):
        """
        Load the last-known SIMKL activity timestamps from addon settings.
        
        These are the timestamps returned by /sync/activities at the end
        of the last successful import sync. Used to detect whether SIMKL
        has new data since we last checked.
        
        Returns:
            dict: Stored activity timestamps, or empty dict if first sync.
                  Keys: 'movies_completed_at', 'tv_shows_watching_at',
                  'tv_shows_completed_at', 'movies_rated_at', 'tv_shows_rated_at'.
        """
        try:
            import xbmcaddon
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            timestamps_json = addon.getSetting('simkl_activity_timestamps')
            
            if timestamps_json:
                result = json.loads(timestamps_json)
                log(f"[sync v{__version__}] SyncManager._load_activity_timestamps() Loaded stored activity timestamps: {result}")
                return result
            
            log(f"[sync v{__version__}] SyncManager._load_activity_timestamps() No stored activity timestamps (first sync)")
            return {}
        except Exception as e:
            log_warning(f"[sync v{__version__}] SyncManager._load_activity_timestamps() Could not load activity timestamps: {e}")
            return {}
    
    def _save_activity_timestamps(self, timestamps):
        """
        Save SIMKL activity timestamps to addon settings after successful sync.

        Args:
            timestamps (dict): Activity timestamps to store. Should contain
                               keys like 'movies_completed_at', 'tv_shows_watching_at',
                               'tv_shows_completed_at', 'movies_rated_at', 'tv_shows_rated_at'.
        """
        try:
            import xbmcaddon
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            timestamps_json = json.dumps(timestamps)
            addon.setSetting('simkl_activity_timestamps', timestamps_json)
            log(f"[sync v{__version__}] SyncManager._save_activity_timestamps() Saved activity timestamps: {timestamps}")
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager._save_activity_timestamps() Failed to save activity timestamps: {e}")
    
    def _check_simkl_activity(self):
        """
        Check SIMKL's /sync/activities endpoint to detect changes.
        
        Compares current activity timestamps against stored values from
        the last successful sync. Returns a dict indicating which categories
        have changed and what date_from value to use for incremental fetch.
        
        Returns:
            dict with keys:
                'movies_changed': bool - True if movies have new activity
                'shows_changed': bool - True if TV shows have new activity  
                'ratings_changed': bool - True if ratings have new activity
                'movies_date_from': str or None - ISO timestamp for incremental movie fetch
                'shows_date_from': str or None - ISO timestamp for incremental show fetch
                'current_activities': dict - Raw response from /sync/activities
                'is_first_sync': bool - True if no stored timestamps exist
        """
        log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Checking SIMKL for changes since last sync...")
        
        # Load stored timestamps from last successful sync
        stored = self._load_activity_timestamps()
        is_first_sync = not stored
        
        # Fetch current activity timestamps from SIMKL
        activities = self.api.get_last_activity()
        
        if not activities:
            log_warning(f"[sync v{__version__}] SyncManager._check_simkl_activity() Failed to fetch /sync/activities - falling back to full sync")
            return {
                'movies_changed': True,
                'shows_changed': True,
                'ratings_changed': True,
                'movies_date_from': None,
                'shows_date_from': None,
                'current_activities': None,
                'is_first_sync': True
            }
        
        log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Current SIMKL activities: {activities}")
        
        # Extract relevant timestamps from the response.
        # SIMKL /sync/activities returns nested objects. The actual field names are:
        #   movies.completed  — updated when a movie is marked watched
        #   tv_shows.watching — updated when a new episode is scrobbled for an in-progress show
        #   tv_shows.completed — updated when a show reaches completed status
        #   movies.rated_at / tv_shows.rated_at — updated on any rating change
        # There is NO "watched_at" field on movies or tv_shows; using that name always
        # returns '' which permanently prevents import from ever triggering.
        movies_activity = activities.get('movies', {})
        shows_activity = activities.get('tv_shows', {})

        current_movies_completed = movies_activity.get('completed', '')
        current_shows_watching = shows_activity.get('watching', '')
        current_shows_completed = shows_activity.get('completed', '')
        current_movies_rated = movies_activity.get('rated_at', '')
        current_shows_rated = shows_activity.get('rated_at', '')

        # Compare against stored timestamps
        stored_movies_completed = stored.get('movies_completed_at', '')
        stored_shows_watching = stored.get('tv_shows_watching_at', '')
        stored_shows_completed = stored.get('tv_shows_completed_at', '')
        stored_movies_rated = stored.get('movies_rated_at', '')
        stored_shows_rated = stored.get('tv_shows_rated_at', '')

        movies_changed = (current_movies_completed != stored_movies_completed)
        shows_watching_changed = (current_shows_watching != stored_shows_watching)
        shows_completed_changed = (current_shows_completed != stored_shows_completed)
        shows_changed = shows_watching_changed or shows_completed_changed
        ratings_changed = (current_movies_rated != stored_movies_rated or
                          current_shows_rated != stored_shows_rated)

        # For incremental fetch, use the stored completed timestamp as date_from.
        # Watching shows are always fetched in full (no date_from) because SIMKL's
        # date_from on the watching endpoint filters by list-entry date, not watch date.
        # If first sync (no stored timestamps), date_from stays None for full fetch.
        movies_date_from = stored_movies_completed if stored_movies_completed and not is_first_sync else None
        shows_date_from = stored_shows_completed if stored_shows_completed and not is_first_sync else None

        log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Changes detected: "
            f"movies={movies_changed}, shows={shows_changed} "
            f"(watching={shows_watching_changed}, completed={shows_completed_changed}), "
            f"ratings={ratings_changed}, is_first_sync={is_first_sync}")

        if movies_changed:
            log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Movie activity changed: "
                f"stored='{stored_movies_completed}' -> current='{current_movies_completed}' | "
                f"date_from={movies_date_from}")
        if shows_changed:
            log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Show activity changed: "
                f"watching: stored='{stored_shows_watching}' -> current='{current_shows_watching}' | "
                f"completed: stored='{stored_shows_completed}' -> current='{current_shows_completed}' | "
                f"date_from={shows_date_from}")

        # Build the new timestamps dict to save after successful sync
        new_timestamps = {
            'movies_completed_at': current_movies_completed,
            'tv_shows_watching_at': current_shows_watching,
            'tv_shows_completed_at': current_shows_completed,
            'movies_rated_at': current_movies_rated,
            'tv_shows_rated_at': current_shows_rated
        }
        
        return {
            'movies_changed': movies_changed,
            'shows_changed': shows_changed,
            'ratings_changed': ratings_changed,
            'movies_date_from': movies_date_from,
            'shows_date_from': shows_date_from,
            'current_activities': new_timestamps,
            'is_first_sync': is_first_sync
        }
    
    # ========== Kodi-Side Delta Sync Tracking ==========
    
    def _get_sync_state_key(self, category):
        """
        Get the setting key for storing sync state.
        
        Args:
            category (str): 'movies' or 'episodes'
            
        Returns:
            str: Setting key name
        """
        return f'last_sync_state_{category}'
    
    def _load_sync_state(self, category):
        """
        Load the last sync state from settings.
        
        Args:
            category (str): 'movies' or 'episodes'
            
        Returns:
            dict: Previous sync state or empty dict
        """
        try:
            import xbmcaddon
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            key = self._get_sync_state_key(category)
            state_json = addon.getSetting(key)
            
            if state_json:
                return json.loads(state_json)
            return {}
        except Exception as e:
            log_debug(f"[sync v{__version__}] SyncManager._load_sync_state() Could not load sync state for {category}: {e}")
            return {}
    
    def _save_sync_state(self, category, state):
        """
        Save current sync state to settings.
        
        Args:
            category (str): 'movies' or 'episodes'
            state (dict): Sync state to save
        """
        try:
            import xbmcaddon
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            key = self._get_sync_state_key(category)
            state_json = json.dumps(state)
            addon.setSetting(key, state_json)
            log_debug(f"[sync v{__version__}] SyncManager._save_sync_state() Saved sync state for {category}")
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager._save_sync_state() Failed to save sync state for {category}: {e}")
    
    def _get_stable_show_key(self, tvshowid, kodi_shows):
        """
        Return a stable string key for a TV show, for use in episode delta state.

        Kodi's internal tvshowid is a SQLite row ID that can change after a library
        rebuild or a forced rescan. Using it as the delta key causes ALL episodes to
        appear "changed" after a rescan (old tvshowid keys never match). Instead we
        use the show's external ID (TVDB → IMDb → TMDb in preference order), which
        is content-stable across rescans. Falls back to tvshowid only when no
        external ID is available.

        Args:
            tvshowid (int): Kodi internal show ID
            kodi_shows (dict): tvshowid → show dict from get_kodi_tvshows(), or None

        Returns:
            str: e.g. "tvdb:81189", "imdb:tt0903747", or "tvshowid:42"
        """
        if kodi_shows and tvshowid in kodi_shows:
            show = kodi_shows[tvshowid]
            uid = show.get('uniqueid', {})
            if uid.get('tvdb'):
                return f"tvdb:{uid['tvdb']}"
            if uid.get('imdb'):
                return f"imdb:{uid['imdb']}"
            if uid.get('tmdb'):
                return f"tmdb:{uid['tmdb']}"
            # imdbnumber on TV shows is often a raw TVDB integer (not a tt* IMDb
            # ID) depending on which scraper populated it. Only treat it as an
            # IMDb key when it starts with 'tt'; otherwise ignore it to avoid a
            # bogus imdb: key that will never match anything.
            imdbnumber = show.get('imdbnumber', '')
            if imdbnumber and imdbnumber.startswith('tt'):
                return f"imdb:{imdbnumber}"
        # Fallback: tvshowid is a SQLite row ID reassigned after library rebuilds.
        # Warn once per show per sync run — _get_stable_show_key is called once
        # per episode so without deduplication a 50-episode show floods the log.
        if tvshowid not in self._warned_tvshowids:
            self._warned_tvshowids.add(tvshowid)
            log_warning(f"[sync v{__version__}] SyncManager._get_stable_show_key() "
                        f"No external ID for tvshowid {tvshowid} — episode delta will "
                        f"reset after a library rescan for this show")
        return f"tvshowid:{tvshowid}"

    def _save_ratings_cache(self, media_type, ratings_list):
        """
        Persist a flat {id_key: rating} lookup to addon settings so the rating
        dialog can resolve current ratings instantly without an API call.

        Written after every ratings fetch during sync (both export and import
        paths). The dialog reads this cache before falling back to the API.

        Args:
            media_type (str): "movies" or "shows"
            ratings_list (list): Raw list returned by api.get_user_ratings()
        """
        try:
            cache = {}
            for item in ratings_list:
                # Use 'is not None' (not 'or') so a hypothetical rating of 0
                # doesn't fall through to the secondary field incorrectly.
                user_rating = item.get('user_rating')
                rating = user_rating if user_rating is not None else item.get('rating')
                if not rating:
                    continue
                if media_type == 'movies':
                    ids = item.get('movie', {}).get('ids', {})
                else:
                    ids = item.get('show', {}).get('ids', {})
                # Store under every available ID so lookup hits regardless of
                # which ID the caller has
                if ids.get('imdb'):
                    cache[f"imdb:{ids['imdb']}"] = rating
                if ids.get('simkl'):
                    cache[f"simkl:{ids['simkl']}"] = rating
                if ids.get('tmdb'):
                    cache[f"tmdb:{ids['tmdb']}"] = rating
                if ids.get('tvdb'):
                    cache[f"tvdb:{ids['tvdb']}"] = rating
            serialized = json.dumps(cache)
            # Warn if the cache is approaching Kodi's settings XML size ceiling.
            # Truncated JSON causes silent cache misses → 4s API call on every
            # rating dialog open.
            if len(serialized) > 65536:
                log_warning(f"[sync v{__version__}] SyncManager._save_ratings_cache() "
                            f"Ratings cache is {len(serialized)} bytes — may be truncated "
                            f"by Kodi settings on some platforms")
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            addon.setSetting(f'rating_cache_{media_type}', serialized)
            log_debug(f"[sync v{__version__}] SyncManager._save_ratings_cache() "
                      f"Cached {len(cache)} {media_type} rating lookups "
                      f"({len(serialized)} bytes)")
        except Exception as e:
            log_warning(f"[sync v{__version__}] SyncManager._save_ratings_cache() "
                        f"Failed to write ratings cache: {e}")

    def _build_movie_state(self, movies):
        """
        Build a state dict from movie list for delta comparison.
        
        Args:
            movies (list): List of Kodi movies
            
        Returns:
            dict: {movie_id: playcount} for all movies with IDs
        """
        state = {}
        for movie in movies:
            # Use IMDb ID as primary key (most reliable)
            movie_id = None
            if movie.get('uniqueid', {}).get('imdb'):
                movie_id = movie['uniqueid']['imdb']
            elif movie.get('imdbnumber', '').startswith('tt'):
                movie_id = movie['imdbnumber']
            
            if movie_id:
                state[movie_id] = movie.get('playcount', 0)
        
        return state
    
    def _build_episode_state(self, episodes, kodi_shows=None):
        """
        Build a state dict from episode list for delta comparison.

        Keys use a stable external show ID (TVDB/IMDb/TMDb) rather than Kodi's
        internal tvshowid, which is re-assigned after library rebuilds and would
        cause all episodes to appear "changed" after every rescan.

        Args:
            episodes (list): List of Kodi episodes
            kodi_shows (dict): tvshowid → show dict from get_kodi_tvshows(). When
                provided, enables stable key resolution. Pass None only if the shows
                dict is genuinely unavailable.

        Returns:
            dict: {"tvdb:81189:1:1": playcount, ...}
        """
        state = {}
        for ep in episodes:
            tvshowid = ep.get('tvshowid')
            season = ep.get('season', 0)
            episode = ep.get('episode', 0)

            if tvshowid is None:
                continue

            show_key = self._get_stable_show_key(tvshowid, kodi_shows)
            key = f"{show_key}:{season}:{episode}"
            state[key] = ep.get('playcount', 0)

        return state
    
    def _find_changed_movies(self, current_movies, last_state):
        """
        Find movies that have changed since last sync.
        
        Args:
            current_movies (list): Current movie list
            last_state (dict): Previous sync state
            
        Returns:
            list: Movies that have changed
        """
        if not last_state:
            log(f"[sync v{__version__}] SyncManager._find_changed_movies() No previous sync state - syncing all movies")
            return current_movies
        
        changed = []
        current_state = self._build_movie_state(current_movies)
        
        for movie in current_movies:
            movie_id = None
            if movie.get('uniqueid', {}).get('imdb'):
                movie_id = movie['uniqueid']['imdb']
            elif movie.get('imdbnumber', '').startswith('tt'):
                movie_id = movie['imdbnumber']
            
            if not movie_id:
                continue
            
            current_playcount = current_state.get(movie_id, 0)
            last_playcount = last_state.get(movie_id, -1)
            
            # Changed if: new movie, or playcount changed
            if last_playcount == -1 or current_playcount != last_playcount:
                changed.append(movie)
        
        log(f"[sync v{__version__}] SyncManager._find_changed_movies() Delta sync: {len(changed)} of {len(current_movies)} movies changed")
        return changed
    
    def _find_changed_episodes(self, current_episodes, last_state, kodi_shows=None):
        """
        Find episodes that have changed since last sync.

        Uses stable external show IDs (via _get_stable_show_key) so that a Kodi
        library rescan — which reassigns internal tvshowid values — does not cause
        every episode to appear "changed".

        Args:
            current_episodes (list): Current episode list
            last_state (dict): Previous sync state (keyed by stable show ID)
            kodi_shows (dict): tvshowid → show dict; passed to _build_episode_state

        Returns:
            list: Episodes that have changed
        """
        if not last_state:
            log(f"[sync v{__version__}] SyncManager._find_changed_episodes() No previous sync state - syncing all episodes")
            return current_episodes

        changed = []
        for ep in current_episodes:
            tvshowid = ep.get('tvshowid')
            season = ep.get('season', 0)
            episode = ep.get('episode', 0)

            if tvshowid is None:
                continue

            show_key = self._get_stable_show_key(tvshowid, kodi_shows)
            key = f"{show_key}:{season}:{episode}"
            # Read playcount directly from the episode object — avoids building
            # a full state dict (and calling _get_stable_show_key twice per ep).
            current_playcount = ep.get('playcount', 0)
            last_playcount = last_state.get(key, -1)

            # Changed if: new episode (not in last state), or playcount changed
            if last_playcount == -1 or current_playcount != last_playcount:
                changed.append(ep)

        log(f"[sync v{__version__}] SyncManager._find_changed_episodes() Delta sync: {len(changed)} of {len(current_episodes)} episodes changed")
        return changed
    
    # ========== Kodi JSON-RPC Methods ==========
    
    def _kodi_rpc(self, method, params=None):
        """
        Execute a Kodi JSON-RPC request.
        
        This is how we interrogate Kodi about its deepest secrets.
        
        Args:
            method (str): JSON-RPC method name
            params (dict): Method parameters
            
        Returns:
            dict: Response result or None on error
        """
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method
        }
        
        if params:
            request["params"] = params
        
        try:
            response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
            
            if "error" in response:
                log_error(f"[sync v{__version__}] SyncManager._kodi_rpc() JSON-RPC error: {response['error']}")
                return None
            
            return response.get("result")
            
        except json.JSONDecodeError as e:
            log_error(f"[sync v{__version__}] SyncManager._kodi_rpc() JSON-RPC parse error: {e}")
            return None
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager._kodi_rpc() JSON-RPC exception: {e}")
            return None
    
    def get_kodi_movies(self):
        """
        Get all movies from Kodi library with watch status.
        
        Returns:
            list: List of movie dicts with IDs and playcount
        """
        log(f"[sync v{__version__}] SyncManager.get_kodi_movies() Fetching movies from Kodi library...")
        
        result = self._kodi_rpc("VideoLibrary.GetMovies", {
            "properties": [
                "title",
                "year",
                "imdbnumber",
                "uniqueid",
                "playcount",
                "lastplayed",
                "file",
                "runtime",
                "userrating"
            ]
        })
        
        if not result or "movies" not in result:
            log_warning(f"[sync v{__version__}] SyncManager.get_kodi_movies() No movies found in Kodi library")
            return []
        
        movies = result["movies"]
        log(f"[sync v{__version__}] SyncManager.get_kodi_movies() Found {len(movies)} movies in Kodi library")
        
        return movies
    
    def get_kodi_episodes(self):
        """
        Get all TV episodes from Kodi library with watch status.
        
        Returns:
            list: List of episode dicts with IDs and playcount
        """
        log(f"[sync v{__version__}] SyncManager.get_kodi_episodes() Fetching TV episodes from Kodi library...")
        
        result = self._kodi_rpc("VideoLibrary.GetEpisodes", {
            "properties": [
                "title",
                "showtitle",
                "season",
                "episode",
                "uniqueid",
                "playcount",
                "lastplayed",
                "file",
                "runtime",
                "tvshowid",
                "userrating"
            ]
        })
        
        if not result or "episodes" not in result:
            log_warning(f"[sync v{__version__}] SyncManager.get_kodi_episodes() No TV episodes found in Kodi library")
            return []
        
        episodes = result["episodes"]
        log(f"[sync v{__version__}] SyncManager.get_kodi_episodes() Found {len(episodes)} episodes in Kodi library")
        
        return episodes
    
    def get_kodi_tvshows(self):
        """
        Get all TV shows from Kodi library.
        
        Returns:
            dict: Map of tvshowid -> show info
        """
        log(f"[sync v{__version__}] SyncManager.get_kodi_tvshows() Fetching TV shows from Kodi library...")
        
        result = self._kodi_rpc("VideoLibrary.GetTVShows", {
            "properties": [
                "title",
                "year",
                "imdbnumber",
                "uniqueid",
                "userrating"
            ]
        })
        
        if not result or "tvshows" not in result:
            log_warning(f"[sync v{__version__}] SyncManager.get_kodi_tvshows() No TV shows found in Kodi library")
            return {}
        
        # Create lookup by tvshowid
        shows = {}
        for show in result["tvshows"]:
            shows[show["tvshowid"]] = show
        
        log(f"[sync v{__version__}] SyncManager.get_kodi_tvshows() Found {len(shows)} TV shows in Kodi library")
        
        return shows
    
    # ========== ID Extraction ==========
    
    def _extract_ids(self, item):
        """
        Extract media IDs from a Kodi item.
        
        Kodi stores IDs in various places depending on how the media was scraped.
        This method tries to find IMDb, TMDb, or TVDB IDs wherever they hide.
        
        Args:
            item (dict): Kodi media item
            
        Returns:
            dict: SIMKL-formatted IDs object
        """
        ids = {}
        
        # Check uniqueid dict (modern Kodi)
        if "uniqueid" in item and item["uniqueid"]:
            uniqueid = item["uniqueid"]
            
            if "imdb" in uniqueid and uniqueid["imdb"]:
                ids["imdb"] = uniqueid["imdb"]  # IMDb IDs are strings ("tt1234567"); never cast to int

            if "tmdb" in uniqueid and uniqueid["tmdb"]:
                # SIMKL API requires tmdb as integer; Kodi may deliver string or int
                try:
                    ids["tmdb"] = int(uniqueid["tmdb"])
                except (ValueError, TypeError):
                    pass  # skip malformed ID

            if "tvdb" in uniqueid and uniqueid["tvdb"]:
                # SIMKL API requires tvdb as integer; Kodi may deliver string or int
                try:
                    ids["tvdb"] = int(uniqueid["tvdb"])
                except (ValueError, TypeError):
                    pass  # skip malformed ID

        # Check imdbnumber field (older Kodi / fallback)
        if "imdbnumber" in item and item["imdbnumber"]:
            imdb = item["imdbnumber"]

            # IMDb IDs start with 'tt'
            if imdb.startswith("tt"):
                ids["imdb"] = imdb
            # Otherwise might be TVDB ID stored as a digit string by older scrapers.
            # isdigit() guarantees int() succeeds here; SIMKL requires integer.
            elif imdb.isdigit():
                if "tvdb" not in ids:
                    ids["tvdb"] = int(imdb)
        
        return ids if ids else None
    
    # ========== Export: Kodi → SIMKL ==========
    
    def export_movies_to_simkl(self):
        """
        Export watched movies from Kodi to SIMKL.
        
        Gets all movies with playcount > 0 and sends them to SIMKL's
        history endpoint. Only sends movies that have valid IDs.
        
        Uses delta sync to only export changed movies.
        
        Returns:
            int: Number of movies exported
        """
        log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() === Starting Movie Export to SIMKL ===")
        
        # Get Kodi movies
        kodi_movies = self.get_kodi_movies()
        
        if not kodi_movies:
            log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() No movies to export")
            return 0
        
        # Load last sync state and find changes (or use all if forced full sync)
        if self.force_full_sync:
            log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() FULL SYNC forced - skipping delta detection")
            changed_movies = kodi_movies
        else:
            last_state = self._load_sync_state('movies')
            changed_movies = self._find_changed_movies(kodi_movies, last_state)
        
        # Filter to watched movies only
        watched_movies = [m for m in changed_movies if m.get("playcount", 0) > 0]
        log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Found {len(watched_movies)} watched movies (changed since last sync)")
        
        if not watched_movies:
            log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() No changed watched movies to export")
            # Still update sync state to track current state
            current_state = self._build_movie_state(kodi_movies)
            self._save_sync_state('movies', current_state)
            return 0
        
        # Build SIMKL payload
        movies_to_send = []
        skipped = 0
        
        for movie in watched_movies:
            ids = self._extract_ids(movie)
            
            if not ids:
                log_debug(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Skipping '{movie.get('title')}' - no valid IDs")
                skipped += 1
                continue
            
            # Build movie object for SIMKL
            movie_obj = {
                "title": movie.get("title", "Unknown"),
                "year": movie.get("year"),
                "ids": ids
            }
            
            # Add watched_at if we have lastplayed
            if movie.get("lastplayed"):
                # Kodi stores lastplayed in local time - convert to UTC for SIMKL
                lastplayed = movie["lastplayed"]
                if lastplayed and lastplayed != "":
                    utc_timestamp = _kodi_time_to_utc_iso(lastplayed)
                    if utc_timestamp:
                        movie_obj["watched_at"] = utc_timestamp
            
            movies_to_send.append(movie_obj)
            log_debug(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Prepared: {movie_obj['title']} ({movie_obj.get('year', '?')})")
        
        if skipped > 0:
            log_warning(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Skipped {skipped} movies without valid IDs")
        
        if not movies_to_send:
            log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() No movies with valid IDs to export")
            # Update sync state
            current_state = self._build_movie_state(kodi_movies)
            self._save_sync_state('movies', current_state)
            return 0
        
        # Send to SIMKL in batches (API may have limits)
        batch_size = 100
        total_sent = 0
        
        for i in range(0, len(movies_to_send), batch_size):
            batch = movies_to_send[i:i + batch_size]
            
            log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Sending batch {i // batch_size + 1}: {len(batch)} movies")
            
            result = self.api.add_to_history(movies=batch)
            
            if result:
                added = result.get("added", {}).get("movies", 0)
                total_sent += added
                log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Batch complete: {added} movies added to SIMKL")
            else:
                log_error(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() Failed to send batch to SIMKL")
                self.stats['errors'] += 1
        
        self.stats['movies_exported'] = total_sent
        
        # Save current sync state after successful export
        current_state = self._build_movie_state(kodi_movies)
        self._save_sync_state('movies', current_state)
        
        log(f"[sync v{__version__}] SyncManager.export_movies_to_simkl() === Movie Export Complete: {total_sent} movies sent to SIMKL ===")
        
        return total_sent
    
    def export_episodes_to_simkl(self):
        """
        Export watched TV episodes from Kodi to SIMKL.
        
        This is more complex than movies because we need to group
        episodes by show and include show-level IDs.
        
        Uses delta sync to only export changed episodes.
        
        Returns:
            int: Number of episodes exported
        """
        log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() === Starting TV Episode Export to SIMKL ===")
        
        # Get TV shows for ID lookup
        tv_shows = self.get_kodi_tvshows()
        
        # Get episodes
        kodi_episodes = self.get_kodi_episodes()
        
        if not kodi_episodes:
            log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() No episodes to export")
            return 0
        
        # Load last sync state and find changes (or use all if forced full sync)
        if self.force_full_sync:
            log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() FULL SYNC forced - skipping delta detection")
            changed_episodes = kodi_episodes
        else:
            last_state = self._load_sync_state('episodes')
            changed_episodes = self._find_changed_episodes(kodi_episodes, last_state, tv_shows)

        # Filter to watched episodes
        watched_episodes = [e for e in changed_episodes if e.get("playcount", 0) > 0]
        log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Found {len(watched_episodes)} watched episodes (changed since last sync)")

        if not watched_episodes:
            log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() No changed watched episodes to export")
            # Still update sync state with stable keys so next delta is accurate
            current_state = self._build_episode_state(kodi_episodes, tv_shows)
            self._save_sync_state('episodes', current_state)
            return 0
        
        # Group episodes by show
        shows_data = {}  # show_id -> {show_info, episodes}
        skipped = 0
        
        for ep in watched_episodes:
            tvshowid = ep.get("tvshowid")
            
            # Get show info
            show = tv_shows.get(tvshowid, {})
            show_ids = self._extract_ids(show) if show else None
            
            if not show_ids:
                # Try to get IDs from episode uniqueid
                show_ids = self._extract_ids(ep)
            
            if not show_ids:
                log_debug(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Skipping '{ep.get('showtitle')}' S{ep.get('season')}E{ep.get('episode')} - no show IDs")
                skipped += 1
                continue
            
            # Create show entry if needed
            show_key = str(tvshowid)
            if show_key not in shows_data:
                shows_data[show_key] = {
                    "title": ep.get("showtitle") or show.get("title", "Unknown"),
                    "year": show.get("year"),
                    "ids": show_ids,
                    "seasons": []
                }
            
            # Find or create season
            season_num = ep.get("season", 0)
            season = None
            for s in shows_data[show_key]["seasons"]:
                if s["number"] == season_num:
                    season = s
                    break
            
            if not season:
                season = {"number": season_num, "episodes": []}
                shows_data[show_key]["seasons"].append(season)
            
            # Add episode
            ep_obj = {
                "number": ep.get("episode", 0)
            }
            
            # Add watched_at if available
            if ep.get("lastplayed"):
                lastplayed = ep["lastplayed"]
                if lastplayed and lastplayed != "":
                    utc_timestamp = _kodi_time_to_utc_iso(lastplayed)
                    if utc_timestamp:
                        ep_obj["watched_at"] = utc_timestamp
            
            season["episodes"].append(ep_obj)
        
        if skipped > 0:
            log_warning(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Skipped {skipped} episodes without valid show IDs")
        
        if not shows_data:
            log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() No episodes with valid IDs to export")
            return 0
        
        # Convert to list for API
        shows_to_send = list(shows_data.values())
        
        log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Prepared {len(shows_to_send)} shows with episodes for export")
        
        # Send to SIMKL
        result = self.api.add_to_history(shows=shows_to_send)
        
        total_sent = 0
        if result:
            added = result.get("added", {}).get("episodes", 0)
            total_sent = added
            log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Episodes added to SIMKL: {added}")
            # Save sync state only on API success. Saving after a failure would
            # record current playcounts as "already synced", silently losing those
            # episodes from every future delta until the user forces a full sync.
            current_state = self._build_episode_state(kodi_episodes, tv_shows)
            self._save_sync_state('episodes', current_state)
        else:
            log_error(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Failed to send episodes to SIMKL — sync state NOT updated so episodes will retry next run")
            self.stats['errors'] += 1

        self.stats['episodes_exported'] = total_sent
        # Only count shows when SIMKL actually added episodes.
        # SIMKL returns "added: 0" when all submitted episodes were already
        # present; reporting "N shows" alongside "0 episodes" is misleading.
        self.stats['shows_exported'] = len(shows_data) if total_sent > 0 else 0

        log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() === Episode Export Complete: {total_sent} episodes sent to SIMKL ===")
        
        return total_sent
    
    # ========== Main Sync Entry Points ==========
    
    def sync_to_simkl(self, sync_movies=True, sync_episodes=True):
        """
        Export Kodi watch history to SIMKL.
        
        This is the main entry point for "push my stuff to the cloud" operations.
        
        Args:
            sync_movies (bool): Export movies
            sync_episodes (bool): Export TV episodes
            
        Returns:
            dict: Sync statistics
        """
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() ========================================")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() SIMKL SYNC: Exporting to SIMKL")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() ========================================")
        
        # Check authentication
        if not self.api.access_token:
            log_error(f"[sync v{__version__}] SyncManager.sync_to_simkl() Not authenticated - cannot sync")
            self._notify("SIMKL Sync", "Please authenticate first!")
            return self.stats
        
        # === TOAST: Sync Started ===
        self._notify("SIMKL Sync", "Sync started...")
        
        # Initialize progress dialog if requested
        if self.show_progress:
            self.progress_dialog = xbmcgui.DialogProgress()
            self.progress_dialog.create("SIMKL Sync", "Preparing to sync...")
        
        try:
            # Export movies
            if sync_movies:
                if self.show_progress:
                    self.progress_dialog.update(10, "Exporting movies to SIMKL...")
                    if self.progress_dialog.iscanceled():
                        self.cancelled = True
                        self._notify("SIMKL Sync", "Sync cancelled")
                        return self.stats
                
                self.export_movies_to_simkl()
            
            # Export episodes
            if sync_episodes:
                if self.show_progress:
                    self.progress_dialog.update(50, "Exporting TV episodes to SIMKL...")
                    if self.progress_dialog.iscanceled():
                        self.cancelled = True
                        self._notify("SIMKL Sync", "Sync cancelled")
                        return self.stats
                
                self.export_episodes_to_simkl()
            
            # Export ratings
            if self.show_progress:
                self.progress_dialog.update(80, "Exporting ratings to SIMKL...")
                if self.progress_dialog.iscanceled():
                    self.cancelled = True
                    self._notify("SIMKL Sync", "Sync cancelled")
                    return self.stats
            
            self.export_ratings_to_simkl()
            
            # Done!
            if self.show_progress:
                self.progress_dialog.update(100, "Export complete!")
            
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager.sync_to_simkl() Sync failed with exception: {e}")
            self.stats['errors'] += 1
            self._notify("SIMKL Sync", f"Sync failed: {e}")
        
        finally:
            if self.progress_dialog:
                self.progress_dialog.close()
        
        # === TOAST: Sync Complete ===
        movies = self.stats['movies_exported']
        episodes = self.stats['episodes_exported']
        errors = self.stats['errors']
        
        if errors == 0:
            self._notify("SIMKL Sync Complete", 
                   f"Exported {movies} movies, {episodes} episodes")
        else:
            self._notify("SIMKL Sync Complete", 
                   f"Exported {movies} movies, {episodes} episodes ({errors} errors)")
        
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() ========================================")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() SYNC COMPLETE")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() Movies: {self.stats['movies_exported']}")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() Episodes: {self.stats['episodes_exported']}")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() Errors: {self.stats['errors']}")
        log(f"[sync v{__version__}] SyncManager.sync_to_simkl() ========================================")
        
        return self.stats


    # ========== Import: SIMKL → Kodi ==========
    
    def _set_movie_playcount(self, movie_id, playcount=1):
        """
        Update a movie's playcount in Kodi.
        
        Args:
            movie_id (int): Kodi movie database ID
            playcount (int): New playcount value
            
        Returns:
            bool: Success
        """
        result = self._kodi_rpc("VideoLibrary.SetMovieDetails", {
            "movieid": movie_id,
            "playcount": playcount
        })
        return result is not None
    
    def _set_episode_playcount(self, episode_id, playcount=1):
        """
        Update an episode's playcount in Kodi.
        
        Args:
            episode_id (int): Kodi episode database ID
            playcount (int): New playcount value
            
        Returns:
            bool: Success
        """
        result = self._kodi_rpc("VideoLibrary.SetEpisodeDetails", {
            "episodeid": episode_id,
            "playcount": playcount
        })
        return result is not None
    
    def _match_movie_to_kodi(self, simkl_movie, kodi_movies_by_id):
        """
        Find a SIMKL movie in the Kodi library.
        
        Matches by IMDb, TMDb, or TVDB ID.
        
        Args:
            simkl_movie (dict): Movie object from SIMKL
            kodi_movies_by_id (dict): Kodi movies indexed by various IDs
            
        Returns:
            dict: Kodi movie or None if not found
        """
        movie_data = simkl_movie.get("movie", {})
        ids = movie_data.get("ids", {})
        
        # Try IMDb first (most reliable)
        if ids.get("imdb"):
            imdb = ids["imdb"]
            if imdb in kodi_movies_by_id.get("imdb", {}):
                return kodi_movies_by_id["imdb"][imdb]
        
        # Try TMDb
        if ids.get("tmdb"):
            tmdb = str(ids["tmdb"])
            if tmdb in kodi_movies_by_id.get("tmdb", {}):
                return kodi_movies_by_id["tmdb"][tmdb]
        
        return None
    
    def _match_show_to_kodi(self, simkl_ids, kodi_shows_by_id, show_title=None, show_year=None):
        """
        Find a SIMKL show in the Kodi library.

        Tries ID-based matching first (IMDb → TVDB → TMDb), then falls back to
        title+year matching for shows where SIMKL returns no overlapping ID.
        Both title and year are required for the fallback to avoid false-positive
        matches on remakes/reboots with identical titles.

        Args:
            simkl_ids (dict): IDs from SIMKL show object
            kodi_shows_by_id (dict): Kodi shows indexed by various IDs
            show_title (str, optional): Show title for title+year fallback
            show_year (int, optional): Premiere year for title+year fallback

        Returns:
            dict: Kodi show or None if not found
        """
        # Try IMDb first
        if simkl_ids.get("imdb"):
            imdb = simkl_ids["imdb"]
            if imdb in kodi_shows_by_id.get("imdb", {}):
                return kodi_shows_by_id["imdb"][imdb]

        # Try TVDB
        if simkl_ids.get("tvdb"):
            tvdb = str(simkl_ids["tvdb"])
            if tvdb in kodi_shows_by_id.get("tvdb", {}):
                return kodi_shows_by_id["tvdb"][tvdb]

        # Try TMDb
        if simkl_ids.get("tmdb"):
            tmdb = str(simkl_ids["tmdb"])
            if tmdb in kodi_shows_by_id.get("tmdb", {}):
                return kodi_shows_by_id["tmdb"][tmdb]

        # Title+year fallback: for shows where SIMKL doesn't return an ID that
        # overlaps with what the Kodi scraper recorded (e.g. SIMKL only returns
        # simkl/slug IDs, or Kodi used a different scraper source).
        if show_title and show_year:
            key = (show_title.lower().strip(), int(show_year))
            if key in kodi_shows_by_id.get("title_year", {}):
                log_debug(f"[sync v{__version__}] SyncManager._match_show_to_kodi() "
                          f"Matched by title+year: '{show_title}' ({show_year})")
                return kodi_shows_by_id["title_year"][key]

        return None
    
    def _build_kodi_movie_index(self, kodi_movies):
        """
        Build an index of Kodi movies by their various IDs.
        
        Returns:
            dict: {"imdb": {id: movie}, "tmdb": {id: movie}, ...}
        """
        index = {"imdb": {}, "tmdb": {}, "tvdb": {}}
        
        for movie in kodi_movies:
            # Index by uniqueid
            uniqueid = movie.get("uniqueid", {})
            
            if uniqueid.get("imdb"):
                index["imdb"][uniqueid["imdb"]] = movie
            
            if uniqueid.get("tmdb"):
                index["tmdb"][str(uniqueid["tmdb"])] = movie
            
            # Also check imdbnumber field
            imdbnumber = movie.get("imdbnumber", "")
            if imdbnumber.startswith("tt"):
                index["imdb"][imdbnumber] = movie
        
        return index
    
    def _build_kodi_show_index(self, kodi_shows):
        """
        Build an index of Kodi TV shows by their various IDs.

        Returns:
            dict: {"imdb": {id: show}, "tvdb": {id: show}, "tmdb": {id: show},
                   "title_year": {(title_lower, year_int): show}}
        """
        index = {"imdb": {}, "tmdb": {}, "tvdb": {}, "title_year": {}}

        for show in kodi_shows.values():
            uniqueid = show.get("uniqueid", {})

            if uniqueid.get("imdb"):
                index["imdb"][uniqueid["imdb"]] = show

            if uniqueid.get("tvdb"):
                index["tvdb"][str(uniqueid["tvdb"])] = show

            if uniqueid.get("tmdb"):
                index["tmdb"][str(uniqueid["tmdb"])] = show

            # Check imdbnumber field
            imdbnumber = show.get("imdbnumber", "")
            if imdbnumber.startswith("tt"):
                index["imdb"][imdbnumber] = show
            elif imdbnumber.isdigit():
                index["tvdb"][imdbnumber] = show

            # Title+year fallback — populated for every show that has both fields.
            # Used when SIMKL returns no ID that overlaps with what Kodi scraped.
            title = show.get("title", "")
            year = show.get("year")
            if title and year:
                try:
                    index["title_year"][(title.lower().strip(), int(year))] = show
                except (ValueError, TypeError):
                    pass  # malformed year from Kodi — skip title+year index entry

        return index
    
    def _build_kodi_episode_index(self, kodi_episodes):
        """
        Build an index of Kodi episodes by show ID, season, and episode.
        
        Returns:
            dict: {tvshowid: {season: {episode: episode_obj}}}
        """
        index = {}
        
        for ep in kodi_episodes:
            tvshowid = ep.get("tvshowid")
            season = ep.get("season", 0)
            episode = ep.get("episode", 0)
            
            if tvshowid not in index:
                index[tvshowid] = {}
            
            if season not in index[tvshowid]:
                index[tvshowid][season] = {}
            
            index[tvshowid][season][episode] = ep
        
        return index
    
    def _lookup_by_imdbnumber(self, kodi_item, by_imdb, by_tvdb=None):
        """
        Fallback lookup using kodi_item['imdbnumber'] when uniqueid-based lookup missed.

        Kodi's imdbnumber field contains either:
          - an IMDb ID (starts with 'tt', e.g. "tt0944947")
          - a TVDB ID (all digits, stored by older scrapers or the TVDB scraper)

        This heuristic is centralised here to avoid copy-pasting the same
        startswith/isdigit branches in every import loop.

        Args:
            kodi_item (dict): Kodi media item (movie, show, etc.)
            by_imdb (dict): Lookup dict keyed by IMDb ID string
            by_tvdb (dict or None): Lookup dict keyed by TVDB ID string,
                                    or None if TVDB is not applicable (e.g. movies)

        Returns:
            Matched value from the lookup dict, or None if no match.
        """
        imdbnumber = kodi_item.get("imdbnumber", "")
        # Guard against None (key present with null value) and non-string types
        # (some scrapers or Kodi versions may return an integer). Both would
        # cause AttributeError on .startswith() below.
        if not isinstance(imdbnumber, str) or not imdbnumber:
            return None
        if imdbnumber.startswith("tt"):
            return by_imdb.get(imdbnumber)
        if imdbnumber.isdigit() and by_tvdb is not None:
            return by_tvdb.get(imdbnumber)
        return None

    def import_movies_from_simkl(self, date_from=None):
        """
        Import watched movies from SIMKL to Kodi.
        
        Fetches completed movies from SIMKL and marks matching
        items in Kodi library as watched.
        
        If 'unmark_not_on_simkl' setting is enabled, also unmarks
        items that are watched in Kodi but not on SIMKL.
        
        Args:
            date_from: Optional ISO 8601 timestamp for incremental fetch.
                       If provided, only fetches movies changed after this date.
                       If None, fetches ALL completed movies (full sync).
                       Per SIMKL team feedback: use /sync/activities timestamps
                       to determine this value.
        
        Returns:
            int: Number of movies marked as watched
        """
        sync_mode = f"incremental from {date_from}" if date_from else "FULL"
        log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() === Starting Movie Import from SIMKL ({sync_mode}) ===")
        
        # Get completed movies from SIMKL (with optional date filter)
        simkl_movies = self.api.get_all_items("movies", "completed", date_from=date_from)
        
        if not simkl_movies:
            log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() No completed movies on SIMKL")
            simkl_movies = []  # Empty list for unmark logic
        else:
            log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Found {len(simkl_movies)} completed movies on SIMKL")
        
        # Get Kodi movies
        kodi_movies = self.get_kodi_movies()
        
        if not kodi_movies:
            log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() No movies in Kodi library to match")
            return 0
        
        # Build SIMKL movie lookup indexes and the unmark ID set in a single pass.
        # Keying by SIMKL IDs lets the outer loop iterate Kodi's (smaller) movie
        # list and do an O(1) lookup per movie, rather than iterating SIMKL's
        # (potentially much larger) list and searching Kodi for each entry.
        simkl_by_imdb = {}          # imdb_id          → simkl_movie entry
        simkl_by_tmdb = {}          # tmdb_id           → simkl_movie entry
        simkl_by_title_year = {}    # (title_lower, yr) → simkl_movie entry
        simkl_movie_ids = set()     # (type, value) pairs used by the unmark pass

        for simkl_movie in simkl_movies:
            movie_data = simkl_movie.get("movie", {})
            ids = movie_data.get("ids", {})
            title = movie_data.get("title", "")
            year = movie_data.get("year")
            if ids.get("imdb"):
                imdb_key = str(ids["imdb"])
                if imdb_key in simkl_by_imdb:
                    log_warning(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() "
                                f"Duplicate IMDb ID {imdb_key} in SIMKL data ('{title}') — keeping later entry")
                simkl_by_imdb[imdb_key] = simkl_movie
                simkl_movie_ids.add(("imdb", ids["imdb"]))
            if ids.get("tmdb"):
                tmdb_key = str(ids["tmdb"])
                if tmdb_key in simkl_by_tmdb:
                    log_warning(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() "
                                f"Duplicate TMDb ID {tmdb_key} in SIMKL data ('{title}') — keeping later entry")
                simkl_by_tmdb[tmdb_key] = simkl_movie
                simkl_movie_ids.add(("tmdb", tmdb_key))
            if title and year:
                try:
                    simkl_by_title_year[(title.lower().strip(), int(year))] = simkl_movie
                except (ValueError, TypeError):
                    log_warning(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() "
                                f"Could not parse year {year!r} for '{title}' — skipping title+year index")

        # Match and update — iterate Kodi movies and look each one up in the
        # SIMKL index.  Kodi is the authoritative local library; anything not
        # in Kodi is irrelevant regardless of what SIMKL knows about it.
        imported = 0
        already_watched = 0
        not_found = 0

        for kodi_movie in kodi_movies:
            uniqueid = kodi_movie.get("uniqueid", {})
            # Use "" not "Unknown" — a falsy default means the title+year guard
            # below correctly skips the lookup rather than searching for ("unknown", year)
            title = kodi_movie.get("title", "")

            # Look up this Kodi movie in the SIMKL index
            simkl_movie = None
            if uniqueid.get("imdb"):
                simkl_movie = simkl_by_imdb.get(str(uniqueid["imdb"]))
            if not simkl_movie and uniqueid.get("tmdb"):
                simkl_movie = simkl_by_tmdb.get(str(uniqueid["tmdb"]))
            if not simkl_movie:
                # imdbnumber field fallback (older scrapers / Kodi versions)
                simkl_movie = self._lookup_by_imdbnumber(kodi_movie, simkl_by_imdb)
            if not simkl_movie:
                # Title+year fallback: for movies where ID sets don't overlap
                kodi_year = kodi_movie.get("year")
                if title and kodi_year:
                    try:
                        simkl_movie = simkl_by_title_year.get(
                            (title.lower().strip(), int(kodi_year))
                        )
                    except (ValueError, TypeError):
                        pass  # malformed year — skip title+year lookup

            title_display = title or kodi_movie.get("title", "Unknown")
            if not simkl_movie:
                log_debug(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Not on SIMKL: {title_display}")
                not_found += 1
                continue

            # Check if already watched in Kodi
            if kodi_movie.get("playcount", 0) > 0:
                already_watched += 1
                continue

            # Mark as watched in Kodi
            movie_id = kodi_movie.get("movieid")

            if self._set_movie_playcount(movie_id, 1):
                log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Marked as watched: {title_display}")
                imported += 1
            else:
                log_error(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Failed to update: {title_display}")
                self.stats['errors'] += 1

        log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Import results: {imported} marked, {already_watched} already watched, {not_found} not on SIMKL")
        
        # Check if we should unmark items not on SIMKL
        # IMPORTANT: Only unmark during FULL sync (no date_from filter).
        # During incremental sync, we only fetched a subset of items from SIMKL,
        # so the absence of an item does NOT mean it's not on SIMKL - it just
        # means it wasn't changed since date_from. Unmarking during incremental
        # sync would incorrectly remove valid watched status.
        if get_setting_bool('unmark_not_on_simkl'):
            if date_from:
                log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Skipping unmark check - incremental sync only has partial data")
            else:
                unmarked = self._unmark_movies_not_on_simkl(kodi_movies, simkl_movie_ids)
                self.stats['movies_unmarked'] = unmarked
                log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Unmarked {unmarked} movies not found on SIMKL")
        
        self.stats['movies_imported'] = imported
        
        log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() === Movie Import Complete: {imported} movies marked as watched ===")
        return imported
    
    def _unmark_movies_not_on_simkl(self, kodi_movies, simkl_movie_ids):
        """
        Unmark movies in Kodi that are watched but not on SIMKL.
        
        Args:
            kodi_movies (list): All movies from Kodi
            simkl_movie_ids (set): Set of (id_type, id_value) tuples from SIMKL
            
        Returns:
            int: Number of movies unmarked
        """
        log(f"[sync v{__version__}] SyncManager._unmark_movies_not_on_simkl() Checking for movies to unmark (not on SIMKL)...")
        unmarked = 0
        
        for movie in kodi_movies:
            # Skip if not watched
            if movie.get("playcount", 0) == 0:
                continue
            
            # Check if this movie is on SIMKL
            uniqueid = movie.get("uniqueid", {})
            found_on_simkl = False
            
            # Check IMDb
            if uniqueid.get("imdb"):
                if ("imdb", uniqueid["imdb"]) in simkl_movie_ids:
                    found_on_simkl = True
            
            # Check TMDb
            if not found_on_simkl and uniqueid.get("tmdb"):
                if ("tmdb", str(uniqueid["tmdb"])) in simkl_movie_ids:
                    found_on_simkl = True
            
            # If not on SIMKL, unmark it
            if not found_on_simkl:
                movie_id = movie.get("movieid")
                title = movie.get("title", "Unknown")
                
                if self._set_movie_playcount(movie_id, 0):
                    log(f"[sync v{__version__}] SyncManager._unmark_movies_not_on_simkl() Unmarked (not on SIMKL): {title}")
                    unmarked += 1
                else:
                    log_error(f"[sync v{__version__}] SyncManager._unmark_movies_not_on_simkl() Failed to unmark: {title}")
                    self.stats['errors'] += 1
        
        return unmarked

    def import_episodes_from_simkl(self, date_from=None):
        """
        Import watched TV episodes from SIMKL to Kodi.
        
        Fetches completed shows from SIMKL, matches them to Kodi,
        and marks individual episodes as watched.
        
        Args:
            date_from: Optional ISO 8601 timestamp for incremental fetch.
                       If provided, only fetches shows changed after this date.
                       If None, fetches ALL shows (full sync).
                       Per SIMKL team feedback: use /sync/activities timestamps
                       to determine this value.
        
        Returns:
            int: Number of episodes marked as watched
        """
        sync_mode = f"incremental from {date_from}" if date_from else "FULL"
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() === Starting Episode Import from SIMKL ({sync_mode}) ===")
        
        # Get completed shows from SIMKL with extended=True to receive per-episode data.
        # Without extended=full the API returns only show-level metadata (title, ids,
        # watched_episodes_count) — the seasons/episodes array needed to mark individual
        # Kodi episodes is absent, so nothing gets imported.
        simkl_shows = self.api.get_all_items("shows", "completed", extended=True, date_from=date_from)
        
        # Fetch ALL watching shows - intentionally no date_from here.
        #
        # Root cause of cross-device sync failure (v7.5.9 fix):
        # SIMKL's date_from filter on /sync/all-items/shows/watching acts on the
        # timestamp of the show's WATCHLIST ENTRY (i.e. when the show was first
        # added to the user's list), NOT on when individual episodes were watched.
        #
        # A show that has been in "watching" status for weeks will NOT appear in
        # the filtered response even if Device A watched new episodes yesterday,
        # because the show's list-entry timestamp pre-dates date_from.  This caused
        # every incremental background sync on Devices B/C/D to silently skip all
        # in-progress series and never import newly watched episodes.
        #
        # The activity check in sync_from_simkl() already gates this call: if
        # shows_changed is False we never reach here at all, so always fetching the
        # full watching list only incurs the extra API payload when something has
        # actually changed on SIMKL.  date_from IS still applied to completed shows
        # (where the status transition itself is reliably timestamped).
        # extended=True is required here: the /sync/all-items/shows/watching endpoint
        # only returns show-level metadata by default.  Per-episode watched status
        # (the seasons[] array) is only included with ?extended=full, which is what
        # extended=True maps to.  Without it every in-progress show hits the
        # "if not seasons: continue" path and 0 episodes are ever imported —
        # the root cause of cross-device episode sync failing for watching shows.
        simkl_watching = self.api.get_all_items("shows", "watching", extended=True)
        
        # Deduplicate before combining — a show theoretically cannot appear in both
        # completed and watching simultaneously, but if it does the watching entry
        # (more recently updated) takes precedence.  Dedup key is "simkl:{id}" when
        # a SIMKL ID is present, otherwise "imdb:{id}" as a fallback.  Shows with
        # neither ID are always included (no reliable dedup key).
        _seen_dedup_keys = set()
        all_shows = []
        _raw_total = len(simkl_watching or []) + len(simkl_shows or [])
        for _show in (simkl_watching or []) + (simkl_shows or []):
            _show_data = _show.get("show", {})
            _ids = _show_data.get("ids", {})
            _sid = _ids.get("simkl")
            _imdb = _ids.get("imdb")
            _dedup_key = (f"simkl:{_sid}" if _sid else
                          f"imdb:{_imdb}" if _imdb else None)
            if _dedup_key:
                if _dedup_key in _seen_dedup_keys:
                    log_warning(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                                f"Duplicate show in completed+watching lists "
                                f"('{_show_data.get('title', '?')}', key={_dedup_key}) — "
                                f"keeping watching entry")
                    continue
                _seen_dedup_keys.add(_dedup_key)
            all_shows.append(_show)

        if not all_shows:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No shows with watched episodes on SIMKL")
            return 0

        _dupes_removed = _raw_total - len(all_shows)
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
            f"Found {len(all_shows)} shows on SIMKL "
            f"({len(simkl_shows or [])} completed, {len(simkl_watching or [])} watching"
            f"{f', {_dupes_removed} deduped' if _dupes_removed else ''})")
        
        # Get Kodi shows and episodes
        kodi_shows = self.get_kodi_tvshows()
        kodi_episodes = self.get_kodi_episodes()
        
        if not kodi_shows:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No TV shows in Kodi library")
            return 0
        
        if not kodi_episodes:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No episodes in Kodi library")
            return 0
        
        # Build Kodi-side indexes (show_index still needed by the unmark pass below)
        show_index = self._build_kodi_show_index(kodi_shows)
        episode_index = self._build_kodi_episode_index(kodi_episodes)

        # Single pass: build the show-level skip set AND count unwatched episodes.
        # Previously two separate comprehensions iterated kodi_episodes twice.
        kodi_shows_with_unwatched = set()
        unwatched_count = 0
        for _ep in kodi_episodes:
            if _ep.get("playcount", 0) == 0 and _ep.get("tvshowid") is not None:
                kodi_shows_with_unwatched.add(_ep.get("tvshowid"))
                unwatched_count += 1
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Kodi library: "
            f"{len(kodi_episodes)} total episodes, {unwatched_count} unwatched "
            f"(diagnostic only — per-episode playcount check drives import logic) "
            f"across {len(kodi_shows_with_unwatched)} shows")

        # Build SIMKL show lookup indexes keyed by each show's IDs so the outer
        # loop can drive on Kodi's ~100-200 shows rather than SIMKL's potentially
        # 600+ shows (most of which will never be in the local library).
        simkl_by_imdb = {}         # imdb_id          → simkl_show entry
        simkl_by_tvdb = {}         # tvdb_id           → simkl_show entry
        simkl_by_tmdb = {}         # tmdb_id           → simkl_show entry
        simkl_by_title_year = {}   # (title_lower, yr) → simkl_show entry

        for simkl_show in all_shows:
            show_data = simkl_show.get("show", {})
            ids = show_data.get("ids", {})
            title = show_data.get("title", "")
            year = show_data.get("year")
            if ids.get("imdb"):
                imdb_key = str(ids["imdb"])
                if imdb_key in simkl_by_imdb:
                    log_warning(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                                f"Duplicate IMDb ID {imdb_key} in SIMKL data ('{title}') — keeping later entry")
                simkl_by_imdb[imdb_key] = simkl_show
            if ids.get("tvdb"):
                tvdb_key = str(ids["tvdb"])
                if tvdb_key in simkl_by_tvdb:
                    log_warning(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                                f"Duplicate TVDB ID {tvdb_key} in SIMKL data ('{title}') — keeping later entry")
                simkl_by_tvdb[tvdb_key] = simkl_show
            if ids.get("tmdb"):
                tmdb_key = str(ids["tmdb"])
                if tmdb_key in simkl_by_tmdb:
                    log_warning(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                                f"Duplicate TMDb ID {tmdb_key} in SIMKL data ('{title}') — keeping later entry")
                simkl_by_tmdb[tmdb_key] = simkl_show
            if title and year:
                try:
                    simkl_by_title_year[(title.lower().strip(), int(year))] = simkl_show
                except (ValueError, TypeError):
                    log_warning(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                                f"Could not parse year {year!r} for '{title}' — skipping title+year index")

        # Match and update — iterate Kodi shows and look each one up in the
        # SIMKL index.  Kodi is the authoritative local library; anything not
        # in Kodi is irrelevant regardless of what SIMKL knows about it.
        imported = 0
        already_watched = 0
        not_found_shows = 0
        # Counts SIMKL episodes where no matching Kodi episode exists at all
        # (distinct from already_watched, which means Kodi has it but playcount>0)
        simkl_eps_not_in_kodi = 0

        kodi_show_list = list(kodi_shows.values())
        total_shows = len(kodi_show_list)
        HEARTBEAT_INTERVAL = 10

        for show_idx, kodi_show in enumerate(kodi_show_list):
            kodi_tvshowid = kodi_show.get("tvshowid")
            # Use "" not "Unknown" so the title+year guard below correctly skips
            # a lookup rather than searching for ("unknown", year)
            show_title = kodi_show.get("title", "")
            show_title_display = show_title or "Unknown"
            uniqueid = kodi_show.get("uniqueid", {})

            # Update progress dialog (50%->80% range for this phase)
            if self.progress_dialog:
                pct = 50 + int((show_idx / max(1, total_shows)) * 30)
                msg = (
                    f"Importing TV episodes from SIMKL... ({show_idx + 1} of {total_shows})\n"
                    f"Processing: {show_title_display}\n"
                    f"Episodes marked so far: {imported}"
                )
                self.progress_dialog.update(pct, msg)
                if self.progress_dialog.iscanceled():
                    self.cancelled = True
                    log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                        f"Cancelled by user at show {show_idx + 1} of {total_shows} "
                        f"({imported} episodes marked)")
                    break

            # Heartbeat: confirm the process is alive every N shows
            if show_idx > 0 and show_idx % HEARTBEAT_INTERVAL == 0:
                log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
                    f"Heartbeat: {show_idx}/{total_shows} shows processed, "
                    f"{imported} episodes marked so far")

            # Look up this Kodi show in the SIMKL index
            simkl_show = None
            if uniqueid.get("imdb"):
                simkl_show = simkl_by_imdb.get(str(uniqueid["imdb"]))
            if not simkl_show and uniqueid.get("tvdb"):
                simkl_show = simkl_by_tvdb.get(str(uniqueid["tvdb"]))
            if not simkl_show and uniqueid.get("tmdb"):
                simkl_show = simkl_by_tmdb.get(str(uniqueid["tmdb"]))
            if not simkl_show:
                # imdbnumber field fallback (older scrapers / Kodi versions)
                simkl_show = self._lookup_by_imdbnumber(kodi_show, simkl_by_imdb, simkl_by_tvdb)
            if not simkl_show:
                # Title+year fallback: for shows where ID sets don't overlap
                kodi_show_year = kodi_show.get("year")
                if show_title and kodi_show_year:
                    try:
                        simkl_show = simkl_by_title_year.get(
                            (show_title.lower().strip(), int(kodi_show_year))
                        )
                    except (ValueError, TypeError):
                        pass  # malformed year — skip title+year lookup

            if not simkl_show:
                log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Not on SIMKL: {show_title_display}")
                not_found_shows += 1
                continue

            # Skip shows where every Kodi episode is already watched
            if kodi_tvshowid not in kodi_shows_with_unwatched:
                log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() All Kodi episodes already watched: {show_title_display}")
                continue

            # Get watched seasons from SIMKL
            seasons = simkl_show.get("seasons", [])
            if not seasons:
                log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No season data from SIMKL for: {show_title_display}")
                continue

            # Process each season
            for season_data in seasons:
                season_num = season_data.get("number", 0)
                episodes = season_data.get("episodes", [])

                for ep_data in episodes:
                    ep_num = ep_data.get("number", 0)

                    # Look up the episode in Kodi by (show, season, episode).
                    kodi_ep = episode_index.get(kodi_tvshowid, {}).get(season_num, {}).get(ep_num)
                    if kodi_ep is None:
                        # SIMKL knows about this episode but Kodi doesn't have it
                        # (e.g. not downloaded, different episode numbering scheme)
                        simkl_eps_not_in_kodi += 1
                        continue

                    # Only write — and only count — when the playcount actually
                    # differs from what Kodi currently has.  This is the single
                    # source of truth: the counter means "episodes genuinely
                    # changed in Kodi", not "episodes evaluated from SIMKL".
                    current_playcount = kodi_ep.get("playcount", 0)
                    if current_playcount > 0:
                        already_watched += 1
                        continue

                    ep_id = kodi_ep.get("episodeid")

                    if self._set_episode_playcount(ep_id, 1):
                        log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Marked: {show_title_display} S{season_num:02d}E{ep_num:02d}")
                        imported += 1
                    else:
                        log_error(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Failed: {show_title_display} S{season_num:02d}E{ep_num:02d}")
                        self.stats['errors'] += 1

        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Import results: {imported} marked, {already_watched} already watched in Kodi")
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() "
            f"Not on SIMKL: {not_found_shows} Kodi shows | "
            f"SIMKL episodes absent from Kodi library: {simkl_eps_not_in_kodi}")

        # If the user cancelled mid-import, skip the unmark check and return
        # partial results — the unmark logic needs a complete SIMKL dataset.
        if self.cancelled:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Import cancelled; skipping unmark check")
            self.stats['episodes_imported'] = imported
            return imported

        # Check if we should unmark episodes not on SIMKL
        # IMPORTANT: Only unmark during FULL sync (no date_from filter).
        # During incremental sync, we only fetched shows changed since date_from,
        # so the absence of an episode does NOT mean it's not on SIMKL.
        if get_setting_bool('unmark_not_on_simkl'):
            if date_from:
                log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Skipping unmark check - incremental sync only has partial data")
            else:
                # Update the progress dialog before the unmark pass so it doesn't
                # appear frozen at "Show N of N" (79%) while hundreds of
                # SetEpisodeDetails RPCs run. Bumps to 80% with descriptive text.
                if self.progress_dialog:
                    self.progress_dialog.update(80, "Checking for episodes to unmark...")
                # Build set of watched episodes on SIMKL for checking
                simkl_episodes = self._build_simkl_episode_set(all_shows, show_index)
                unmarked = self._unmark_episodes_not_on_simkl(kodi_episodes, simkl_episodes)
                self.stats['episodes_unmarked'] = unmarked
                log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Unmarked {unmarked} episodes not found on SIMKL")

        self.stats['episodes_imported'] = imported

        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() === Episode Import Complete: {imported} episodes marked as watched ===")
        return imported
    
    def _build_simkl_episode_set(self, simkl_shows, kodi_show_index):
        """
        Build a set of (tvshowid, season, episode) tuples for episodes on SIMKL.
        
        Args:
            simkl_shows (list): Shows from SIMKL
            kodi_show_index (dict): Kodi show index for matching
            
        Returns:
            set: Set of (tvshowid, season, episode) tuples
        """
        episode_set = set()
        
        for simkl_show in simkl_shows:
            show_data = simkl_show.get("show", {})
            show_ids = show_data.get("ids", {})

            # Find show in Kodi — try IDs first, fall back to title+year
            kodi_show = self._match_show_to_kodi(
                show_ids, kodi_show_index,
                show_title=show_data.get("title", ""),
                show_year=show_data.get("year")
            )
            if not kodi_show:
                continue
            
            kodi_tvshowid = kodi_show.get("tvshowid")
            seasons = simkl_show.get("seasons", [])
            
            for season_data in seasons:
                season_num = season_data.get("number", 0)
                episodes = season_data.get("episodes", [])
                
                for ep_data in episodes:
                    ep_num = ep_data.get("number", 0)
                    episode_set.add((kodi_tvshowid, season_num, ep_num))
        
        return episode_set
    
    def _unmark_episodes_not_on_simkl(self, kodi_episodes, simkl_episodes):
        """
        Unmark episodes in Kodi that are watched but not on SIMKL.
        
        Args:
            kodi_episodes (list): All episodes from Kodi
            simkl_episodes (set): Set of (tvshowid, season, episode) tuples from SIMKL
            
        Returns:
            int: Number of episodes unmarked
        """
        log(f"[sync v{__version__}] SyncManager._unmark_episodes_not_on_simkl() Checking for episodes to unmark (not on SIMKL)...")
        unmarked = 0
        
        for episode in kodi_episodes:
            # Skip if not watched
            if episode.get("playcount", 0) == 0:
                continue
            
            # Check if this episode is on SIMKL
            tvshowid = episode.get("tvshowid")
            season = episode.get("season", 0)
            episode_num = episode.get("episode", 0)
            
            if (tvshowid, season, episode_num) not in simkl_episodes:
                # Not on SIMKL, unmark it
                episode_id = episode.get("episodeid")
                title = episode.get("showtitle", "Unknown")
                
                if self._set_episode_playcount(episode_id, 0):
                    log(f"[sync v{__version__}] SyncManager._unmark_episodes_not_on_simkl() Unmarked (not on SIMKL): {title} S{season:02d}E{episode_num:02d}")
                    unmarked += 1
                else:
                    log_error(f"[sync v{__version__}] SyncManager._unmark_episodes_not_on_simkl() Failed to unmark: {title} S{season:02d}E{episode_num:02d}")
                    self.stats['errors'] += 1
        
        return unmarked
    
    # ========== Rating Sync ==========
    
    def _set_movie_rating(self, movie_id, rating):
        """
        Update a movie's user rating in Kodi.
        
        Args:
            movie_id (int): Kodi movie database ID
            rating (int): Rating value 0-10 (0 = unrated)
            
        Returns:
            bool: Success
        """
        result = self._kodi_rpc("VideoLibrary.SetMovieDetails", {
            "movieid": movie_id,
            "userrating": rating
        })
        return result is not None
    
    def _set_show_rating(self, tvshowid, rating):
        """
        Update a TV show's user rating in Kodi.
        
        Args:
            tvshowid (int): Kodi TV show database ID
            rating (int): Rating value 0-10 (0 = unrated)
            
        Returns:
            bool: Success
        """
        result = self._kodi_rpc("VideoLibrary.SetTVShowDetails", {
            "tvshowid": tvshowid,
            "userrating": rating
        })
        return result is not None
    
    def export_ratings_to_simkl(self):
        """
        Export user ratings from Kodi to SIMKL (delta sync).
        
        Fetches current SIMKL ratings first, then only sends ratings
        that differ between Kodi and SIMKL. Skips items where the
        rating already matches.
        
        Returns:
            int: Number of ratings actually changed on SIMKL
        """
        log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() === Starting Rating Export to SIMKL ===")
        
        exported = 0
        
        # --- Movie Ratings ---
        # Fetch current SIMKL movie ratings for comparison
        simkl_movie_ratings = {}
        try:
            simkl_movies = self.api.get_user_ratings("movies")
            if simkl_movies is not None:
                # Write cache even when the list is empty — an empty cache is
                # valid (user has no ratings) and prevents the dialog from
                # falling back to a slow API call on every open.
                self._save_ratings_cache('movies', simkl_movies)
            if simkl_movies:
                for item in simkl_movies:
                    movie = item.get("movie", {})
                    ids = movie.get("ids", {})
                    rating = item.get("user_rating", item.get("rating", 0))
                    # Index by imdb for matching
                    imdb = ids.get("imdb")
                    tmdb = ids.get("tmdb")
                    if imdb:
                        simkl_movie_ratings[("imdb", str(imdb))] = rating
                    if tmdb:
                        simkl_movie_ratings[("tmdb", str(tmdb))] = rating
                log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Fetched {len(simkl_movies)} existing SIMKL movie ratings for comparison")
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Failed to fetch SIMKL movie ratings: {e}")
        
        kodi_movies = self.get_kodi_movies()
        changed_movies = []
        
        for movie in kodi_movies:
            rating = movie.get("userrating", 0)
            ids = self._extract_ids(movie)
            if not ids or rating == 0:
                continue
            
            # Check if SIMKL already has this exact rating
            imdb = ids.get("imdb")
            tmdb = ids.get("tmdb")
            simkl_rating = None
            if imdb:
                simkl_rating = simkl_movie_ratings.get(("imdb", str(imdb)))
            if simkl_rating is None and tmdb:
                simkl_rating = simkl_movie_ratings.get(("tmdb", str(tmdb)))
            
            if simkl_rating == rating:
                continue  # Already matches, skip
            
            movie_obj = {
                "title": movie.get("title", "Unknown"),
                "year": movie.get("year"),
                "ids": ids,
                "rating": rating
            }
            changed_movies.append(movie_obj)
        
        if changed_movies:
            log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Exporting {len(changed_movies)} changed movie ratings (skipped {len([m for m in kodi_movies if m.get('userrating', 0) > 0]) - len(changed_movies)} unchanged)")
            result = self.api._request("POST", "/sync/ratings", data={"movies": changed_movies})
            if result:
                added = result.get("added", {}).get("movies", 0)
                exported += added
                log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Movie ratings exported: {added}")
            else:
                log_error(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Failed to export movie ratings")
                self.stats['errors'] += 1
        else:
            log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() No changed movie ratings to export")
        
        # --- Show Ratings ---
        # Fetch current SIMKL show ratings for comparison
        simkl_show_ratings = {}
        try:
            simkl_shows = self.api.get_user_ratings("shows")
            if simkl_shows is not None:
                self._save_ratings_cache('shows', simkl_shows)
            if simkl_shows:
                for item in simkl_shows:
                    show = item.get("show", {})
                    ids = show.get("ids", {})
                    rating = item.get("user_rating", item.get("rating", 0))
                    imdb = ids.get("imdb")
                    tmdb = ids.get("tmdb")
                    tvdb = ids.get("tvdb")
                    if imdb:
                        simkl_show_ratings[("imdb", str(imdb))] = rating
                    if tmdb:
                        simkl_show_ratings[("tmdb", str(tmdb))] = rating
                    if tvdb:
                        simkl_show_ratings[("tvdb", str(tvdb))] = rating
                log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Fetched {len(simkl_shows)} existing SIMKL show ratings for comparison")
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Failed to fetch SIMKL show ratings: {e}")
        
        kodi_shows = self.get_kodi_tvshows()
        changed_shows = []
        
        for tvshowid, show in kodi_shows.items():
            rating = show.get("userrating", 0)
            ids = self._extract_ids(show)
            if not ids or rating == 0:
                continue
            
            # Check if SIMKL already has this exact rating
            imdb = ids.get("imdb")
            tmdb = ids.get("tmdb")
            tvdb = ids.get("tvdb")
            simkl_rating = None
            if imdb:
                simkl_rating = simkl_show_ratings.get(("imdb", str(imdb)))
            if simkl_rating is None and tmdb:
                simkl_rating = simkl_show_ratings.get(("tmdb", str(tmdb)))
            if simkl_rating is None and tvdb:
                simkl_rating = simkl_show_ratings.get(("tvdb", str(tvdb)))
            
            if simkl_rating == rating:
                continue  # Already matches, skip
            
            show_obj = {
                "title": show.get("title", "Unknown"),
                "year": show.get("year"),
                "ids": ids,
                "rating": rating
            }
            changed_shows.append(show_obj)
        
        if changed_shows:
            log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Exporting {len(changed_shows)} changed show ratings (skipped {len([s for s in kodi_shows.values() if s.get('userrating', 0) > 0]) - len(changed_shows)} unchanged)")
            result = self.api._request("POST", "/sync/ratings", data={"shows": changed_shows})
            if result:
                added = result.get("added", {}).get("shows", 0)
                exported += added
                log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Show ratings exported: {added}")
            else:
                log_error(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() Failed to export show ratings")
                self.stats['errors'] += 1
        else:
            log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() No changed show ratings to export")
        
        self.stats['ratings_exported'] = exported
        log(f"[sync v{__version__}] SyncManager.export_ratings_to_simkl() === Rating Export Complete: {exported} ratings changed ===")
        return exported
    
    def import_ratings_from_simkl(self):
        """
        Import user ratings from SIMKL to Kodi.
        
        Fetches all user ratings from SIMKL and writes them to Kodi's
        userrating field. Items rated in Kodi but not on SIMKL get cleared to 0.
        
        Returns:
            int: Number of ratings updated
        """
        log(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() === Starting Rating Import from SIMKL ===")
        
        imported = 0
        
        # --- Movie Ratings ---
        simkl_movie_ratings = self.api.get_user_ratings("movies")
        if simkl_movie_ratings is not None:
            self._save_ratings_cache('movies', simkl_movie_ratings)
        kodi_movies = self.get_kodi_movies()
        kodi_movie_index = self._build_kodi_movie_index(kodi_movies)

        if simkl_movie_ratings:
            log(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Found {len(simkl_movie_ratings)} movie ratings on SIMKL")

            for item in simkl_movie_ratings:
                movie_data = item.get("movie", item)
                ids = movie_data.get("ids", {})
                rating = item.get("user_rating", item.get("rating", 0))

                # Guard against non-numeric, zero, bool, or out-of-range values.
                # bool is a subclass of int in Python (True→1, False→0) so we
                # exclude it explicitly. SIMKL's scale is 1–10; floats truncate.
                if not isinstance(rating, (int, float)) or isinstance(rating, bool) or rating <= 0:
                    continue
                simkl_rating = int(rating)
                if not 1 <= simkl_rating <= 10:
                    title_hint = movie_data.get("title", "?")
                    log_warning(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() "
                                f"Movie rating {simkl_rating} out of 1–10 for '{title_hint}' — skipping")
                    continue

                # Find in Kodi
                kodi_movie = self._match_movie_to_kodi(item, kodi_movie_index)
                if not kodi_movie:
                    continue

                kodi_rating = kodi_movie.get("userrating", 0)

                if kodi_rating != simkl_rating:
                    movie_id = kodi_movie.get("movieid")
                    if self._set_movie_rating(movie_id, simkl_rating):
                        title = kodi_movie.get("title", "Unknown")
                        log_debug(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Movie rating: {title} -> {simkl_rating}/10")
                        imported += 1
                    else:
                        self.stats['errors'] += 1
        
        # NOTE: We intentionally do NOT clear Kodi movie ratings for movies absent
        # from SIMKL's ratings list.  Absence from the ratings list can mean either
        # (a) the user never rated the movie on SIMKL, or (b) the movie isn't on
        # SIMKL at all.  We have no way to distinguish these cases, so clearing
        # would destroy ratings the user set via Kodi, Trakt, or any other source.
        # Rating import is additive-only: SIMKL ratings come in, nothing goes out.

        # --- Show Ratings ---
        # Build a SIMKL ratings index keyed by each show's IDs so the outer
        # loop can drive on Kodi's shows (Kodi-first, same pattern as episode/
        # movie import).  This also fixes the clearing-pass bug: the old code
        # built simkl_rated_show_ids from ID tuples only, so any show that was
        # matched via title+year during the import pass had no entry in the set,
        # and its rating was immediately cleared in the clearing pass.  With the
        # index approach, import and clearing happen in the same loop so there
        # is no mismatch between what was matched and what is checked.
        simkl_show_ratings = self.api.get_user_ratings("shows")
        if simkl_show_ratings is not None:
            self._save_ratings_cache('shows', simkl_show_ratings)
        kodi_shows = self.get_kodi_tvshows()

        simkl_show_rating_by_imdb = {}         # imdb_id          → int rating
        simkl_show_rating_by_tvdb = {}         # tvdb_id           → int rating
        simkl_show_rating_by_tmdb = {}         # tmdb_id           → int rating
        simkl_show_rating_by_title_year = {}   # (title_lower, yr) → int rating

        if simkl_show_ratings:
            log(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Found {len(simkl_show_ratings)} show ratings on SIMKL")
            for item in simkl_show_ratings:
                show_data = item.get("show", item)
                ids = show_data.get("ids", {})
                title = show_data.get("title", "")
                year = show_data.get("year")
                rating = item.get("user_rating", item.get("rating", 0))

                # Guard against non-numeric, zero, bool, or out-of-range values.
                # bool is a subclass of int in Python (True→1, False→0) so we
                # exclude it explicitly. SIMKL's scale is 1–10; floats truncate.
                if not isinstance(rating, (int, float)) or isinstance(rating, bool) or rating <= 0:
                    continue
                rating = int(rating)
                if not 1 <= rating <= 10:
                    log_warning(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() "
                                f"Show rating {rating} out of 1–10 for '{title}' — skipping")
                    continue

                if ids.get("imdb"):
                    imdb_key = str(ids["imdb"])
                    if imdb_key in simkl_show_rating_by_imdb:
                        log_warning(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() "
                                    f"Duplicate IMDb ID {imdb_key} in SIMKL show ratings ('{title}') — keeping later entry")
                    simkl_show_rating_by_imdb[imdb_key] = rating
                if ids.get("tvdb"):
                    simkl_show_rating_by_tvdb[str(ids["tvdb"])] = rating
                if ids.get("tmdb"):
                    simkl_show_rating_by_tmdb[str(ids["tmdb"])] = rating
                if title and year:
                    try:
                        simkl_show_rating_by_title_year[(title.lower().strip(), int(year))] = rating
                    except (ValueError, TypeError):
                        log_warning(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() "
                                    f"Could not parse year {year!r} for '{title}' — skipping title+year index")

        # Single Kodi-first pass: import ratings and clear stale ones together.
        for tvshowid, kodi_show in kodi_shows.items():
            uniqueid = kodi_show.get("uniqueid", {})
            kodi_rating = kodi_show.get("userrating", 0)
            # Use "" not "Unknown" — a falsy default means the title+year guard
            # below correctly skips the lookup rather than searching for ("unknown", year)
            title = kodi_show.get("title", "")
            title_display = title or "Unknown"
            year = kodi_show.get("year")

            # Look up this Kodi show in the SIMKL ratings index
            simkl_rating = None
            if uniqueid.get("imdb"):
                simkl_rating = simkl_show_rating_by_imdb.get(str(uniqueid["imdb"]))
            if simkl_rating is None and uniqueid.get("tvdb"):
                simkl_rating = simkl_show_rating_by_tvdb.get(str(uniqueid["tvdb"]))
            if simkl_rating is None and uniqueid.get("tmdb"):
                simkl_rating = simkl_show_rating_by_tmdb.get(str(uniqueid["tmdb"]))
            if simkl_rating is None:
                simkl_rating = self._lookup_by_imdbnumber(
                    kodi_show, simkl_show_rating_by_imdb, simkl_show_rating_by_tvdb
                )
            if simkl_rating is None and title and year:
                try:
                    simkl_rating = simkl_show_rating_by_title_year.get(
                        (title.lower().strip(), int(year))
                    )
                except (ValueError, TypeError):
                    pass  # malformed year — skip title+year lookup

            # Rating import is additive-only: if SIMKL has no rating for this show,
            # leave the Kodi rating alone.  We cannot distinguish "user never rated
            # on SIMKL" from "show not tracked on SIMKL at all" without a separate
            # API call, and clearing in either case would destroy ratings the user set
            # via Kodi, Trakt, or another source.
            if simkl_rating is not None and kodi_rating != simkl_rating:
                if self._set_show_rating(tvshowid, simkl_rating):
                    log_debug(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Show rating: {title_display} -> {simkl_rating}/10")
                    imported += 1
                else:
                    self.stats['errors'] += 1
        
        self.stats['ratings_imported'] = imported
        log(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() === Rating Import Complete: {imported} ratings updated ===")
        return imported
    
    def sync_from_simkl(self, sync_movies=True, sync_episodes=True):
        """
        Import watch history from SIMKL to Kodi.
        
        This pulls your SIMKL watched items and marks them as watched in Kodi.
        
        Uses SIMKL's /sync/activities endpoint to detect whether anything has
        changed since the last successful sync. If no changes are detected,
        the import is skipped entirely (saving API calls and server load).
        When changes ARE detected, uses ?date_from= to only fetch items that
        changed since the last sync timestamp.
        
        Per SIMKL team feedback (Ennergizer, 2026-02-25): this is the recommended
        approach instead of fetching ALL items every sync.
        
        Args:
            sync_movies (bool): Import movies
            sync_episodes (bool): Import TV episodes
            
        Returns:
            dict: Sync statistics
        """
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() ========================================")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() SIMKL SYNC: Importing from SIMKL")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() ========================================")
        
        # Check authentication
        if not self.api.access_token:
            log_error(f"[sync v{__version__}] SyncManager.sync_from_simkl() Not authenticated - cannot sync")
            self._notify("SIMKL Sync", "Please authenticate first!")
            return self.stats
        
        # === TOAST: Import Started ===
        self._notify("SIMKL Sync", "Importing from SIMKL...")
        
        # Initialize progress dialog if requested
        if self.show_progress:
            self.progress_dialog = xbmcgui.DialogProgress()
            self.progress_dialog.create("SIMKL Sync", "Importing from SIMKL...")
        
        try:
            # ---- Incremental sync: check /sync/activities first ----
            # Unless force_full_sync is set (manual sync), we check whether
            # SIMKL has any new activity before fetching data. This avoids
            # pulling the entire watch history when nothing has changed.
            movies_date_from = None
            shows_date_from = None
            activity_data = None
            
            if self.force_full_sync:
                # Manual sync: always do full fetch (no date_from)
                # This gives users confidence that everything is synchronized
                log(f"[sync v{__version__}] SyncManager.sync_from_simkl() FULL SYNC forced - skipping activity check, fetching all items")
            else:
                # Background/automatic sync: check activities for delta detection
                activity_data = self._check_simkl_activity()
                
                # If nothing has changed on SIMKL, skip the entire import
                if (not activity_data['movies_changed'] and 
                    not activity_data['shows_changed'] and 
                    not activity_data['ratings_changed']):
                    log(f"[sync v{__version__}] SyncManager.sync_from_simkl() No changes detected on SIMKL since last sync - skipping import")
                    
                    if self.show_progress:
                        self.progress_dialog.update(100, "Already in sync - no changes on SIMKL")
                    
                    self._notify("SIMKL Sync", "Already in sync")
                    
                    # Still save timestamps even though nothing changed
                    # (confirms we checked successfully)
                    if activity_data.get('current_activities'):
                        self._save_activity_timestamps(activity_data['current_activities'])
                    
                    return self.stats
                
                # Set date_from for incremental fetch where changes were detected
                if activity_data['movies_changed']:
                    movies_date_from = activity_data.get('movies_date_from')
                if activity_data['shows_changed']:
                    shows_date_from = activity_data.get('shows_date_from')
            
            # Import movies
            if sync_movies:
                if self.show_progress:
                    self.progress_dialog.update(10, "Importing movies from SIMKL...")
                    if self.progress_dialog.iscanceled():
                        self.cancelled = True
                        self._notify("SIMKL Sync", "Import cancelled")
                        return self.stats
                
                # Skip movie import if activity check showed no movie changes
                # (only applies to background sync, not force_full_sync)
                if not self.force_full_sync and activity_data and not activity_data['movies_changed']:
                    log(f"[sync v{__version__}] SyncManager.sync_from_simkl() No movie changes on SIMKL - skipping movie import")
                else:
                    self.import_movies_from_simkl(date_from=movies_date_from)
            
            # Import episodes
            if sync_episodes:
                if self.show_progress:
                    self.progress_dialog.update(50, "Importing TV episodes from SIMKL...")
                    if self.progress_dialog.iscanceled():
                        self.cancelled = True
                        self._notify("SIMKL Sync", "Import cancelled")
                        return self.stats
                
                # Skip episode import if activity check showed no show changes
                if not self.force_full_sync and activity_data and not activity_data['shows_changed']:
                    log(f"[sync v{__version__}] SyncManager.sync_from_simkl() No show changes on SIMKL - skipping episode import")
                else:
                    self.import_episodes_from_simkl(date_from=shows_date_from)
            
            # Import ratings
            if self.show_progress:
                self.progress_dialog.update(80, "Importing ratings from SIMKL...")
                if self.progress_dialog.iscanceled():
                    self.cancelled = True
                    self._notify("SIMKL Sync", "Import cancelled")
                    return self.stats
            
            # Skip rating import if activity check showed no rating changes
            if not self.force_full_sync and activity_data and not activity_data['ratings_changed']:
                log(f"[sync v{__version__}] SyncManager.sync_from_simkl() No rating changes on SIMKL - skipping rating import")
            else:
                self.import_ratings_from_simkl()
            
            # Done!
            if self.show_progress:
                self.progress_dialog.update(100, "Import complete!")
            
            # Save activity timestamps after successful sync
            # This ensures the next sync can use these timestamps for delta detection
            if activity_data and activity_data.get('current_activities'):
                self._save_activity_timestamps(activity_data['current_activities'])
            elif self.force_full_sync:
                # After a forced full sync, fetch and save current activity timestamps
                # so subsequent background syncs can use incremental mode
                log(f"[sync v{__version__}] SyncManager.sync_from_simkl() Full sync complete - fetching activity timestamps for future incremental syncs")
                fresh_activities = self.api.get_last_activity()
                if fresh_activities:
                    movies_activity = fresh_activities.get('movies', {})
                    shows_activity = fresh_activities.get('tv_shows', {})
                    new_timestamps = {
                        'movies_completed_at': movies_activity.get('completed', ''),
                        'tv_shows_watching_at': shows_activity.get('watching', ''),
                        'tv_shows_completed_at': shows_activity.get('completed', ''),
                        'movies_rated_at': movies_activity.get('rated_at', ''),
                        'tv_shows_rated_at': shows_activity.get('rated_at', '')
                    }
                    self._save_activity_timestamps(new_timestamps)
            
        except Exception as e:
            log_error(f"[sync v{__version__}] SyncManager.sync_from_simkl() Import failed with exception: {e}")
            import traceback
            log_error(traceback.format_exc())
            self.stats['errors'] += 1
            self._notify("SIMKL Sync", f"Import failed: {e}")
        
        finally:
            if self.progress_dialog:
                self.progress_dialog.close()
        
        # === TOAST: Import Complete ===
        movies = self.stats['movies_imported']
        episodes = self.stats['episodes_imported']
        errors = self.stats['errors']
        
        if errors == 0:
            self._notify("SIMKL Import Complete", 
                   f"Marked {movies} movies, {episodes} episodes as watched")
        else:
            self._notify("SIMKL Import Complete", 
                   f"Marked {movies} movies, {episodes} episodes ({errors} errors)")
        
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() ========================================")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() IMPORT COMPLETE")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() Movies: {self.stats['movies_imported']}")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() Episodes: {self.stats['episodes_imported']}")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() Errors: {self.stats['errors']}")
        log(f"[sync v{__version__}] SyncManager.sync_from_simkl() ========================================")
        
        return self.stats


# ========== Standalone Execution ==========

def run_sync_to_simkl(silent=False):
    """
    Run export sync with progress dialog.
    
    Called from default.py when user triggers manual sync.
    This is the DEFAULT ACTION when clicking the addon (like Trakt).
    
    Args:
        silent (bool): If True, don't show progress dialog (toasts still appear)
    """
    sync_movies = get_setting_bool("sync_movies_from_kodi")
    sync_episodes = get_setting_bool("sync_episodes_from_kodi")
    
    # Default to both if settings not configured
    if not sync_movies and not sync_episodes:
        sync_movies = True
        sync_episodes = True
    
    # Show progress dialog unless silent mode
    show_progress = not silent
    
    # Manual syncs always use force_full_sync=True to bypass delta detection
    # and give users confidence that everything is being synchronized.
    # Background syncs in service.py use force_full_sync=False (the default)
    # to benefit from incremental sync via /sync/activities.
    manager = SyncManager(show_progress=show_progress, silent=False, force_full_sync=True)
    try:
        manager.sync_to_simkl(sync_movies=sync_movies, sync_episodes=sync_episodes)
    finally:
        manager.close()


def run_sync_from_simkl(silent=False):
    """
    Run import sync with progress dialog.
    
    Called from default.py when user triggers manual import.
    Uses force_full_sync=True to always fetch ALL items from SIMKL
    instead of using incremental /sync/activities delta detection.
    
    Args:
        silent (bool): If True, don't show progress dialog (toasts still appear)
    """
    sync_movies = get_setting_bool("sync_movies_to_kodi")
    sync_episodes = get_setting_bool("sync_episodes_to_kodi")
    
    # Default to both if settings not configured
    if not sync_movies and not sync_episodes:
        sync_movies = True
        sync_episodes = True
    
    # Show progress dialog unless silent mode
    show_progress = not silent
    
    # Manual imports always do full sync for user confidence
    manager = SyncManager(show_progress=show_progress, silent=False, force_full_sync=True)
    try:
        manager.sync_from_simkl(sync_movies=sync_movies, sync_episodes=sync_episodes)
    finally:
        manager.close()
