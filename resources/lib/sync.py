# -*- coding: utf-8 -*-
"""
SIMKL Sync Module
Version: 7.5.7
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
import xbmcgui
from datetime import datetime, timezone
from resources.lib.utils import (
    log, log_error, log_debug, log_warning,
    get_setting_bool, notify
)
from resources.lib.api import SimklAPI

# Module version
__version__ = '7.5.7'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] sync.py v{__version__} - Sync manager module loading', level=xbmc.LOGINFO)


def _kodi_time_to_utc_iso(kodi_timestamp):
    """
    Convert Kodi's local time string to UTC ISO 8601 format.
    
    Kodi stores lastplayed as "YYYY-MM-DD HH:MM:SS" in local time.
    SIMKL expects ISO 8601 with "Z" suffix meaning UTC.
    
    Args:
        kodi_timestamp: String like "2026-01-15 20:30:00"
        
    Returns:
        UTC ISO string like "2026-01-15T22:30:00Z" or None on failure
    """
    try:
        # Parse as local time (naive datetime)
        local_dt = datetime.strptime(kodi_timestamp, "%Y-%m-%d %H:%M:%S")
        # Attach local timezone info
        local_dt = local_dt.replace(tzinfo=datetime.now(timezone.utc).astimezone().tzinfo)
        # Convert to UTC
        utc_dt = local_dt.astimezone(timezone.utc)
        # Format as ISO 8601 with Z suffix
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
        import xbmcaddon
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
                  Keys are dotted paths like 'movies.watched_at',
                  'tv_shows.watched_at', etc.
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
                               keys like 'movies_watched_at', 'tv_shows_watched_at',
                               'movies_rated_at', 'tv_shows_rated_at'.
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
        
        # Extract relevant timestamps from the response
        # SIMKL /sync/activities returns nested objects like:
        # {"movies": {"watched_at": "...", "rated_at": "..."},
        #  "tv_shows": {"watched_at": "...", "rated_at": "..."}}
        movies_activity = activities.get('movies', {})
        shows_activity = activities.get('tv_shows', {})
        
        current_movies_watched = movies_activity.get('watched_at', '')
        current_shows_watched = shows_activity.get('watched_at', '')
        current_movies_rated = movies_activity.get('rated_at', '')
        current_shows_rated = shows_activity.get('rated_at', '')
        
        # Compare against stored timestamps
        stored_movies_watched = stored.get('movies_watched_at', '')
        stored_shows_watched = stored.get('tv_shows_watched_at', '')
        stored_movies_rated = stored.get('movies_rated_at', '')
        stored_shows_rated = stored.get('tv_shows_rated_at', '')
        
        movies_changed = (current_movies_watched != stored_movies_watched)
        shows_changed = (current_shows_watched != stored_shows_watched)
        ratings_changed = (current_movies_rated != stored_movies_rated or
                          current_shows_rated != stored_shows_rated)
        
        # For incremental fetch, use the stored timestamp as date_from.
        # If first sync (no stored timestamps), date_from stays None for full fetch.
        movies_date_from = stored_movies_watched if stored_movies_watched and not is_first_sync else None
        shows_date_from = stored_shows_watched if stored_shows_watched and not is_first_sync else None
        
        log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Changes detected: "
            f"movies={movies_changed}, shows={shows_changed}, ratings={ratings_changed}, "
            f"is_first_sync={is_first_sync}")
        
        if movies_changed:
            log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Movie activity changed: "
                f"stored='{stored_movies_watched}' -> current='{current_movies_watched}' | "
                f"date_from={movies_date_from}")
        if shows_changed:
            log(f"[sync v{__version__}] SyncManager._check_simkl_activity() Show activity changed: "
                f"stored='{stored_shows_watched}' -> current='{current_shows_watched}' | "
                f"date_from={shows_date_from}")
        
        # Build the new timestamps dict to save after successful sync
        new_timestamps = {
            'movies_watched_at': current_movies_watched,
            'tv_shows_watched_at': current_shows_watched,
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
    
    def _build_episode_state(self, episodes):
        """
        Build a state dict from episode list for delta comparison.
        
        Args:
            episodes (list): List of Kodi episodes
            
        Returns:
            dict: {show_id:season:episode: playcount}
        """
        state = {}
        for ep in episodes:
            tvshowid = ep.get('tvshowid')
            season = ep.get('season', 0)
            episode = ep.get('episode', 0)
            
            if tvshowid is not None:
                key = f"{tvshowid}:{season}:{episode}"
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
    
    def _find_changed_episodes(self, current_episodes, last_state):
        """
        Find episodes that have changed since last sync.
        
        Args:
            current_episodes (list): Current episode list
            last_state (dict): Previous sync state
            
        Returns:
            list: Episodes that have changed
        """
        if not last_state:
            log(f"[sync v{__version__}] SyncManager._find_changed_episodes() No previous sync state - syncing all episodes")
            return current_episodes
        
        changed = []
        current_state = self._build_episode_state(current_episodes)
        
        for ep in current_episodes:
            tvshowid = ep.get('tvshowid')
            season = ep.get('season', 0)
            episode = ep.get('episode', 0)
            
            if tvshowid is None:
                continue
            
            key = f"{tvshowid}:{season}:{episode}"
            current_playcount = current_state.get(key, 0)
            last_playcount = last_state.get(key, -1)
            
            # Changed if: new episode, or playcount changed
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
                ids["imdb"] = uniqueid["imdb"]
            
            if "tmdb" in uniqueid and uniqueid["tmdb"]:
                ids["tmdb"] = str(uniqueid["tmdb"])
            
            if "tvdb" in uniqueid and uniqueid["tvdb"]:
                ids["tvdb"] = str(uniqueid["tvdb"])
        
        # Check imdbnumber field (older Kodi / fallback)
        if "imdbnumber" in item and item["imdbnumber"]:
            imdb = item["imdbnumber"]
            
            # IMDb IDs start with 'tt'
            if imdb.startswith("tt"):
                ids["imdb"] = imdb
            # Otherwise might be TVDB ID (numeric)
            elif imdb.isdigit():
                if "tvdb" not in ids:
                    ids["tvdb"] = imdb
        
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
            changed_episodes = self._find_changed_episodes(kodi_episodes, last_state)
        
        # Filter to watched episodes
        watched_episodes = [e for e in changed_episodes if e.get("playcount", 0) > 0]
        log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Found {len(watched_episodes)} watched episodes (changed since last sync)")
        
        if not watched_episodes:
            log(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() No changed watched episodes to export")
            # Still update sync state
            current_state = self._build_episode_state(kodi_episodes)
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
        else:
            log_error(f"[sync v{__version__}] SyncManager.export_episodes_to_simkl() Failed to send episodes to SIMKL")
            self.stats['errors'] += 1
        
        self.stats['episodes_exported'] = total_sent
        self.stats['shows_exported'] = len(shows_data)
        
        # Save current sync state after successful export
        current_state = self._build_episode_state(kodi_episodes)
        self._save_sync_state('episodes', current_state)
        
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
    
    def _match_show_to_kodi(self, simkl_ids, kodi_shows_by_id):
        """
        Find a SIMKL show in the Kodi library.
        
        Args:
            simkl_ids (dict): IDs from SIMKL show object
            kodi_shows_by_id (dict): Kodi shows indexed by various IDs
            
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
            dict: {"imdb": {id: show}, "tvdb": {id: show}, "tmdb": {id: show}}
        """
        index = {"imdb": {}, "tmdb": {}, "tvdb": {}}
        
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
        
        # Build index for fast lookup
        kodi_index = self._build_kodi_movie_index(kodi_movies)
        
        # Build SIMKL movie ID set for unmark checking
        simkl_movie_ids = set()
        for simkl_movie in simkl_movies:
            movie_data = simkl_movie.get("movie", {})
            ids = movie_data.get("ids", {})
            if ids.get("imdb"):
                simkl_movie_ids.add(("imdb", ids["imdb"]))
            if ids.get("tmdb"):
                simkl_movie_ids.add(("tmdb", str(ids["tmdb"])))
        
        # Match and update
        imported = 0
        already_watched = 0
        not_found = 0
        
        for simkl_movie in simkl_movies:
            # Find in Kodi
            kodi_movie = self._match_movie_to_kodi(simkl_movie, kodi_index)
            
            if not kodi_movie:
                movie_info = simkl_movie.get("movie", {})
                log_debug(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Not in Kodi: {movie_info.get('title', 'Unknown')}")
                not_found += 1
                continue
            
            # Check if already watched in Kodi
            if kodi_movie.get("playcount", 0) > 0:
                already_watched += 1
                continue
            
            # Mark as watched in Kodi
            movie_id = kodi_movie.get("movieid")
            title = kodi_movie.get("title", "Unknown")
            
            if self._set_movie_playcount(movie_id, 1):
                log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Marked as watched: {title}")
                imported += 1
            else:
                log_error(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Failed to update: {title}")
                self.stats['errors'] += 1
        
        log(f"[sync v{__version__}] SyncManager.import_movies_from_simkl() Import results: {imported} marked, {already_watched} already watched, {not_found} not in Kodi")
        
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
        
        # Get completed shows from SIMKL (includes episode info, with optional date filter)
        simkl_shows = self.api.get_all_items("shows", "completed", date_from=date_from)
        
        # Fetch ALL watching shows - intentionally no date_from here.
        #
        # Root cause of cross-device sync failure (v7.5.7 fix):
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
        simkl_watching = self.api.get_all_items("shows", "watching")
        
        all_shows = (simkl_shows or []) + (simkl_watching or [])
        
        if not all_shows:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No shows with watched episodes on SIMKL")
            return 0
        
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Found {len(all_shows)} shows on SIMKL ({len(simkl_shows or [])} completed, {len(simkl_watching or [])} watching)")
        
        # Get Kodi shows and episodes
        kodi_shows = self.get_kodi_tvshows()
        kodi_episodes = self.get_kodi_episodes()
        
        if not kodi_shows:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No TV shows in Kodi library")
            return 0
        
        if not kodi_episodes:
            log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No episodes in Kodi library")
            return 0
        
        # Build indexes
        show_index = self._build_kodi_show_index(kodi_shows)
        episode_index = self._build_kodi_episode_index(kodi_episodes)
        
        # Match and update
        imported = 0
        already_watched = 0
        not_found_shows = 0
        not_found_eps = 0
        matched_shows = set()
        
        for simkl_show in all_shows:
            show_data = simkl_show.get("show", {})
            show_ids = show_data.get("ids", {})
            show_title = show_data.get("title", "Unknown")
            
            # Find show in Kodi
            kodi_show = self._match_show_to_kodi(show_ids, show_index)
            
            if not kodi_show:
                log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Show not in Kodi: {show_title}")
                not_found_shows += 1
                continue
            
            kodi_tvshowid = kodi_show.get("tvshowid")
            
            # Get watched seasons from SIMKL
            # SIMKL can return seasons in different ways
            seasons = simkl_show.get("seasons", [])
            
            if not seasons:
                # Sometimes it's just episode count, no detailed seasons
                log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() No detailed season info for {show_title}")
                continue
            
            # Process each season
            for season_data in seasons:
                season_num = season_data.get("number", 0)
                episodes = season_data.get("episodes", [])
                
                for ep_data in episodes:
                    ep_num = ep_data.get("number", 0)
                    
                    # Find episode in Kodi
                    kodi_ep = None
                    if kodi_tvshowid in episode_index:
                        if season_num in episode_index[kodi_tvshowid]:
                            kodi_ep = episode_index[kodi_tvshowid][season_num].get(ep_num)
                    
                    if not kodi_ep:
                        not_found_eps += 1
                        continue
                    
                    # Check if already watched
                    if kodi_ep.get("playcount", 0) > 0:
                        already_watched += 1
                        continue
                    
                    # Mark as watched
                    ep_id = kodi_ep.get("episodeid")
                    
                    if self._set_episode_playcount(ep_id, 1):
                        log_debug(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Marked: {show_title} S{season_num:02d}E{ep_num:02d}")
                        imported += 1
                        matched_shows.add(kodi_tvshowid)
                    else:
                        log_error(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Failed: {show_title} S{season_num:02d}E{ep_num:02d}")
                        self.stats['errors'] += 1
        
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Import results: {imported} marked, {already_watched} already watched")
        log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Not found: {not_found_shows} shows, {not_found_eps} episodes")
        
        # Check if we should unmark episodes not on SIMKL
        # IMPORTANT: Only unmark during FULL sync (no date_from filter).
        # During incremental sync, we only fetched shows changed since date_from,
        # so the absence of an episode does NOT mean it's not on SIMKL.
        if get_setting_bool('unmark_not_on_simkl'):
            if date_from:
                log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Skipping unmark check - incremental sync only has partial data")
            else:
                # Build set of watched episodes on SIMKL for checking
                simkl_episodes = self._build_simkl_episode_set(all_shows, show_index)
                unmarked = self._unmark_episodes_not_on_simkl(kodi_episodes, simkl_episodes)
                self.stats['episodes_unmarked'] = unmarked
                log(f"[sync v{__version__}] SyncManager.import_episodes_from_simkl() Unmarked {unmarked} episodes not found on SIMKL")
        
        self.stats['episodes_imported'] = imported
        self.stats['shows_imported'] = len(matched_shows)
        
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
            
            # Find show in Kodi
            kodi_show = self._match_show_to_kodi(show_ids, kodi_show_index)
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
        kodi_movies = self.get_kodi_movies()
        kodi_movie_index = self._build_kodi_movie_index(kodi_movies)
        
        # Track which Kodi movies have SIMKL ratings (for clearing unrated)
        simkl_rated_movie_ids = set()
        
        if simkl_movie_ratings:
            log(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Found {len(simkl_movie_ratings)} movie ratings on SIMKL")
            
            for item in simkl_movie_ratings:
                movie_data = item.get("movie", item)
                ids = movie_data.get("ids", {})
                rating = item.get("user_rating", item.get("rating", 0))
                
                if not rating or rating == 0:
                    continue
                
                # Track this movie as rated on SIMKL
                if ids.get("imdb"):
                    simkl_rated_movie_ids.add(("imdb", ids["imdb"]))
                if ids.get("tmdb"):
                    simkl_rated_movie_ids.add(("tmdb", str(ids["tmdb"])))
                
                # Find in Kodi
                kodi_movie = self._match_movie_to_kodi(item, kodi_movie_index)
                if not kodi_movie:
                    continue
                
                kodi_rating = kodi_movie.get("userrating", 0)
                simkl_rating = int(rating)
                
                if kodi_rating != simkl_rating:
                    movie_id = kodi_movie.get("movieid")
                    if self._set_movie_rating(movie_id, simkl_rating):
                        title = kodi_movie.get("title", "Unknown")
                        log_debug(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Movie rating: {title} -> {simkl_rating}/10")
                        imported += 1
                    else:
                        self.stats['errors'] += 1
        
        # Clear ratings for Kodi movies not rated on SIMKL
        for movie in kodi_movies:
            if movie.get("userrating", 0) == 0:
                continue
            
            uniqueid = movie.get("uniqueid", {})
            found_on_simkl = False
            
            if uniqueid.get("imdb"):
                if ("imdb", uniqueid["imdb"]) in simkl_rated_movie_ids:
                    found_on_simkl = True
            if not found_on_simkl and uniqueid.get("tmdb"):
                if ("tmdb", str(uniqueid["tmdb"])) in simkl_rated_movie_ids:
                    found_on_simkl = True
            
            if not found_on_simkl:
                movie_id = movie.get("movieid")
                if self._set_movie_rating(movie_id, 0):
                    title = movie.get("title", "Unknown")
                    log_debug(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Cleared movie rating: {title}")
                    imported += 1
        
        # --- Show Ratings ---
        simkl_show_ratings = self.api.get_user_ratings("shows")
        kodi_shows = self.get_kodi_tvshows()
        kodi_show_index = self._build_kodi_show_index(kodi_shows)
        
        simkl_rated_show_ids = set()
        
        if simkl_show_ratings:
            log(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Found {len(simkl_show_ratings)} show ratings on SIMKL")
            
            for item in simkl_show_ratings:
                show_data = item.get("show", item)
                ids = show_data.get("ids", {})
                rating = item.get("user_rating", item.get("rating", 0))
                
                if not rating or rating == 0:
                    continue
                
                # Track this show as rated on SIMKL
                if ids.get("imdb"):
                    simkl_rated_show_ids.add(("imdb", ids["imdb"]))
                if ids.get("tvdb"):
                    simkl_rated_show_ids.add(("tvdb", str(ids["tvdb"])))
                if ids.get("tmdb"):
                    simkl_rated_show_ids.add(("tmdb", str(ids["tmdb"])))
                
                # Find in Kodi
                kodi_show = self._match_show_to_kodi(ids, kodi_show_index)
                if not kodi_show:
                    continue
                
                kodi_rating = kodi_show.get("userrating", 0)
                simkl_rating = int(rating)
                
                if kodi_rating != simkl_rating:
                    tvshowid = kodi_show.get("tvshowid")
                    if self._set_show_rating(tvshowid, simkl_rating):
                        title = kodi_show.get("title", "Unknown")
                        log_debug(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Show rating: {title} -> {simkl_rating}/10")
                        imported += 1
                    else:
                        self.stats['errors'] += 1
        
        # Clear ratings for Kodi shows not rated on SIMKL
        for tvshowid, show in kodi_shows.items():
            if show.get("userrating", 0) == 0:
                continue
            
            uniqueid = show.get("uniqueid", {})
            found_on_simkl = False
            
            if uniqueid.get("imdb"):
                if ("imdb", uniqueid["imdb"]) in simkl_rated_show_ids:
                    found_on_simkl = True
            if not found_on_simkl and uniqueid.get("tvdb"):
                if ("tvdb", str(uniqueid["tvdb"])) in simkl_rated_show_ids:
                    found_on_simkl = True
            if not found_on_simkl and uniqueid.get("tmdb"):
                if ("tmdb", str(uniqueid["tmdb"])) in simkl_rated_show_ids:
                    found_on_simkl = True
            
            if not found_on_simkl:
                if self._set_show_rating(tvshowid, 0):
                    title = show.get("title", "Unknown")
                    log_debug(f"[sync v{__version__}] SyncManager.import_ratings_from_simkl() Cleared show rating: {title}")
                    imported += 1
        
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
                        'movies_watched_at': movies_activity.get('watched_at', ''),
                        'tv_shows_watched_at': shows_activity.get('watched_at', ''),
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
