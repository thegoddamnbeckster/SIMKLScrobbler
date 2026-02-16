# -*- coding: utf-8 -*-
"""
SIMKL Sync Module
Version: 7.3.4
Last Modified: 2026-02-05

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
__version__ = '7.4.0'

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
        log_warning(f"Failed to convert timestamp '{kodi_timestamp}': {e}")
        return None


class SyncManager:
    """
    Manages synchronization between Kodi and SIMKL.
    
    Think of this as a very organized, slightly OCD librarian who makes sure
    your Kodi library and SIMKL account are saying the same things.
    """
    
    def __init__(self, show_progress=False, silent=False):
        """
        Initialize the sync manager.
        
        Args:
            show_progress (bool): Show progress dialog during sync
            silent (bool): Suppress notifications (for background sync)
        """
        self.api = SimklAPI()
        self.show_progress = show_progress
        self.silent = silent
        self.progress_dialog = None
        self.cancelled = False
        
        # Stats for reporting
        self.stats = {
            'movies_exported': 0,
            'episodes_exported': 0,
            'movies_imported': 0,
            'episodes_imported': 0,
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
            log_debug("SyncManager API session closed")
    
    # ========== Delta Sync Tracking ==========
    
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
            log_debug(f"Could not load sync state for {category}: {e}")
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
            log_debug(f"Saved sync state for {category}")
        except Exception as e:
            log_error(f"Failed to save sync state for {category}: {e}")
    
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
            log("No previous sync state - syncing all movies")
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
        
        log(f"Delta sync: {len(changed)} of {len(current_movies)} movies changed")
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
            log("No previous sync state - syncing all episodes")
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
        
        log(f"Delta sync: {len(changed)} of {len(current_episodes)} episodes changed")
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
                log_error(f"JSON-RPC error: {response['error']}")
                return None
            
            return response.get("result")
            
        except json.JSONDecodeError as e:
            log_error(f"JSON-RPC parse error: {e}")
            return None
        except Exception as e:
            log_error(f"JSON-RPC exception: {e}")
            return None
    
    def get_kodi_movies(self):
        """
        Get all movies from Kodi library with watch status.
        
        Returns:
            list: List of movie dicts with IDs and playcount
        """
        log("Fetching movies from Kodi library...")
        
        result = self._kodi_rpc("VideoLibrary.GetMovies", {
            "properties": [
                "title",
                "year",
                "imdbnumber",
                "uniqueid",
                "playcount",
                "lastplayed",
                "file",
                "runtime"
            ]
        })
        
        if not result or "movies" not in result:
            log_warning("No movies found in Kodi library")
            return []
        
        movies = result["movies"]
        log(f"Found {len(movies)} movies in Kodi library")
        
        return movies
    
    def get_kodi_episodes(self):
        """
        Get all TV episodes from Kodi library with watch status.
        
        Returns:
            list: List of episode dicts with IDs and playcount
        """
        log("Fetching TV episodes from Kodi library...")
        
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
                "tvshowid"
            ]
        })
        
        if not result or "episodes" not in result:
            log_warning("No TV episodes found in Kodi library")
            return []
        
        episodes = result["episodes"]
        log(f"Found {len(episodes)} episodes in Kodi library")
        
        return episodes
    
    def get_kodi_tvshows(self):
        """
        Get all TV shows from Kodi library.
        
        Returns:
            dict: Map of tvshowid -> show info
        """
        log("Fetching TV shows from Kodi library...")
        
        result = self._kodi_rpc("VideoLibrary.GetTVShows", {
            "properties": [
                "title",
                "year",
                "imdbnumber",
                "uniqueid"
            ]
        })
        
        if not result or "tvshows" not in result:
            log_warning("No TV shows found in Kodi library")
            return {}
        
        # Create lookup by tvshowid
        shows = {}
        for show in result["tvshows"]:
            shows[show["tvshowid"]] = show
        
        log(f"Found {len(shows)} TV shows in Kodi library")
        
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
        log("=== Starting Movie Export to SIMKL ===")
        
        # Get Kodi movies
        kodi_movies = self.get_kodi_movies()
        
        if not kodi_movies:
            log("No movies to export")
            return 0
        
        # Load last sync state and find changes
        last_state = self._load_sync_state('movies')
        changed_movies = self._find_changed_movies(kodi_movies, last_state)
        
        # Filter to watched movies only
        watched_movies = [m for m in changed_movies if m.get("playcount", 0) > 0]
        log(f"Found {len(watched_movies)} watched movies (changed since last sync)")
        
        if not watched_movies:
            log("No changed watched movies to export")
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
                log_debug(f"Skipping '{movie.get('title')}' - no valid IDs")
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
            log_debug(f"Prepared: {movie_obj['title']} ({movie_obj.get('year', '?')})")
        
        if skipped > 0:
            log_warning(f"Skipped {skipped} movies without valid IDs")
        
        if not movies_to_send:
            log("No movies with valid IDs to export")
            # Update sync state
            current_state = self._build_movie_state(kodi_movies)
            self._save_sync_state('movies', current_state)
            return 0
        
        # Send to SIMKL in batches (API may have limits)
        batch_size = 100
        total_sent = 0
        
        for i in range(0, len(movies_to_send), batch_size):
            batch = movies_to_send[i:i + batch_size]
            
            log(f"Sending batch {i // batch_size + 1}: {len(batch)} movies")
            
            result = self.api.add_to_history(movies=batch)
            
            if result:
                added = result.get("added", {}).get("movies", 0)
                total_sent += added
                log(f"Batch complete: {added} movies added to SIMKL")
            else:
                log_error("Failed to send batch to SIMKL")
                self.stats['errors'] += 1
        
        self.stats['movies_exported'] = total_sent
        
        # Save current sync state after successful export
        current_state = self._build_movie_state(kodi_movies)
        self._save_sync_state('movies', current_state)
        
        log(f"=== Movie Export Complete: {total_sent} movies sent to SIMKL ===")
        
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
        log("=== Starting TV Episode Export to SIMKL ===")
        
        # Get TV shows for ID lookup
        tv_shows = self.get_kodi_tvshows()
        
        # Get episodes
        kodi_episodes = self.get_kodi_episodes()
        
        if not kodi_episodes:
            log("No episodes to export")
            return 0
        
        # Load last sync state and find changes
        last_state = self._load_sync_state('episodes')
        changed_episodes = self._find_changed_episodes(kodi_episodes, last_state)
        
        # Filter to watched episodes
        watched_episodes = [e for e in changed_episodes if e.get("playcount", 0) > 0]
        log(f"Found {len(watched_episodes)} watched episodes (changed since last sync)")
        
        if not watched_episodes:
            log("No changed watched episodes to export")
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
                log_debug(f"Skipping '{ep.get('showtitle')}' S{ep.get('season')}E{ep.get('episode')} - no show IDs")
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
            log_warning(f"Skipped {skipped} episodes without valid show IDs")
        
        if not shows_data:
            log("No episodes with valid IDs to export")
            return 0
        
        # Convert to list for API
        shows_to_send = list(shows_data.values())
        
        log(f"Prepared {len(shows_to_send)} shows with episodes for export")
        
        # Send to SIMKL
        result = self.api.add_to_history(shows=shows_to_send)
        
        total_sent = 0
        if result:
            added = result.get("added", {}).get("episodes", 0)
            total_sent = added
            log(f"Episodes added to SIMKL: {added}")
        else:
            log_error("Failed to send episodes to SIMKL")
            self.stats['errors'] += 1
        
        self.stats['episodes_exported'] = total_sent
        
        # Save current sync state after successful export
        current_state = self._build_episode_state(kodi_episodes)
        self._save_sync_state('episodes', current_state)
        
        log(f"=== Episode Export Complete: {total_sent} episodes sent to SIMKL ===")
        
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
        log("========================================")
        log("  SIMKL SYNC: Exporting to SIMKL")
        log("========================================")
        
        # Check authentication
        if not self.api.access_token:
            log_error("Not authenticated - cannot sync")
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
            
            # Done!
            if self.show_progress:
                self.progress_dialog.update(100, "Sync complete!")
            
        except Exception as e:
            log_error(f"Sync failed with exception: {e}")
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
        
        log("========================================")
        log(f"  SYNC COMPLETE")
        log(f"  Movies: {self.stats['movies_exported']}")
        log(f"  Episodes: {self.stats['episodes_exported']}")
        log(f"  Errors: {self.stats['errors']}")
        log("========================================")
        
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
    
    def import_movies_from_simkl(self):
        """
        Import watched movies from SIMKL to Kodi.
        
        Fetches completed movies from SIMKL and marks matching
        items in Kodi library as watched.
        
        If 'unmark_not_on_simkl' setting is enabled, also unmarks
        items that are watched in Kodi but not on SIMKL.
        
        Returns:
            int: Number of movies marked as watched
        """
        log("=== Starting Movie Import from SIMKL ===")
        
        # Get completed movies from SIMKL
        simkl_movies = self.api.get_all_items("movies", "completed")
        
        if not simkl_movies:
            log("No completed movies on SIMKL")
            simkl_movies = []  # Empty list for unmark logic
        else:
            log(f"Found {len(simkl_movies)} completed movies on SIMKL")
        
        # Get Kodi movies
        kodi_movies = self.get_kodi_movies()
        
        if not kodi_movies:
            log("No movies in Kodi library to match")
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
                log_debug(f"Not in Kodi: {movie_info.get('title', 'Unknown')}")
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
                log(f"Marked as watched: {title}")
                imported += 1
            else:
                log_error(f"Failed to update: {title}")
                self.stats['errors'] += 1
        
        log(f"Import results: {imported} marked, {already_watched} already watched, {not_found} not in Kodi")
        
        # Check if we should unmark items not on SIMKL
        if get_setting_bool('unmark_not_on_simkl'):
            unmarked = self._unmark_movies_not_on_simkl(kodi_movies, simkl_movie_ids)
            log(f"Unmarked {unmarked} movies not found on SIMKL")
        
        self.stats['movies_imported'] = imported
        
        log(f"=== Movie Import Complete: {imported} movies marked as watched ===")
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
        log("Checking for movies to unmark (not on SIMKL)...")
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
                    log(f"Unmarked (not on SIMKL): {title}")
                    unmarked += 1
                else:
                    log_error(f"Failed to unmark: {title}")
                    self.stats['errors'] += 1
        
        return unmarked

    def import_episodes_from_simkl(self):
        """
        Import watched TV episodes from SIMKL to Kodi.
        
        Fetches completed shows from SIMKL, matches them to Kodi,
        and marks individual episodes as watched.
        
        Returns:
            int: Number of episodes marked as watched
        """
        log("=== Starting Episode Import from SIMKL ===")
        
        # Get completed shows from SIMKL (includes episode info)
        simkl_shows = self.api.get_all_items("shows", "completed")
        
        # Also get "watching" shows - they have watched episodes too!
        simkl_watching = self.api.get_all_items("shows", "watching")
        
        all_shows = (simkl_shows or []) + (simkl_watching or [])
        
        if not all_shows:
            log("No shows with watched episodes on SIMKL")
            return 0
        
        log(f"Found {len(all_shows)} shows on SIMKL ({len(simkl_shows or [])} completed, {len(simkl_watching or [])} watching)")
        
        # Get Kodi shows and episodes
        kodi_shows = self.get_kodi_tvshows()
        kodi_episodes = self.get_kodi_episodes()
        
        if not kodi_shows:
            log("No TV shows in Kodi library")
            return 0
        
        if not kodi_episodes:
            log("No episodes in Kodi library")
            return 0
        
        # Build indexes
        show_index = self._build_kodi_show_index(kodi_shows)
        episode_index = self._build_kodi_episode_index(kodi_episodes)
        
        # Match and update
        imported = 0
        already_watched = 0
        not_found_shows = 0
        not_found_eps = 0
        
        for simkl_show in all_shows:
            show_data = simkl_show.get("show", {})
            show_ids = show_data.get("ids", {})
            show_title = show_data.get("title", "Unknown")
            
            # Find show in Kodi
            kodi_show = self._match_show_to_kodi(show_ids, show_index)
            
            if not kodi_show:
                log_debug(f"Show not in Kodi: {show_title}")
                not_found_shows += 1
                continue
            
            kodi_tvshowid = kodi_show.get("tvshowid")
            
            # Get watched seasons from SIMKL
            # SIMKL can return seasons in different ways
            seasons = simkl_show.get("seasons", [])
            
            if not seasons:
                # Sometimes it's just episode count, no detailed seasons
                log_debug(f"No detailed season info for {show_title}")
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
                        log_debug(f"Marked: {show_title} S{season_num:02d}E{ep_num:02d}")
                        imported += 1
                    else:
                        log_error(f"Failed: {show_title} S{season_num:02d}E{ep_num:02d}")
                        self.stats['errors'] += 1
        
        log(f"Import results: {imported} marked, {already_watched} already watched")
        log(f"Not found: {not_found_shows} shows, {not_found_eps} episodes")
        
        # Check if we should unmark episodes not on SIMKL
        if get_setting_bool('unmark_not_on_simkl'):
            # Build set of watched episodes on SIMKL for checking
            simkl_episodes = self._build_simkl_episode_set(all_shows, show_index)
            unmarked = self._unmark_episodes_not_on_simkl(kodi_episodes, simkl_episodes)
            log(f"Unmarked {unmarked} episodes not found on SIMKL")
        
        self.stats['episodes_imported'] = imported
        
        log(f"=== Episode Import Complete: {imported} episodes marked as watched ===")
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
        log("Checking for episodes to unmark (not on SIMKL)...")
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
                    log(f"Unmarked (not on SIMKL): {title} S{season:02d}E{episode_num:02d}")
                    unmarked += 1
                else:
                    log_error(f"Failed to unmark: {title} S{season:02d}E{episode_num:02d}")
                    self.stats['errors'] += 1
        
        return unmarked
    
    def sync_from_simkl(self, sync_movies=True, sync_episodes=True):
        """
        Import watch history from SIMKL to Kodi.
        
        This pulls your SIMKL watched items and marks them as watched in Kodi.
        
        Args:
            sync_movies (bool): Import movies
            sync_episodes (bool): Import TV episodes
            
        Returns:
            dict: Sync statistics
        """
        log("========================================")
        log("  SIMKL SYNC: Importing from SIMKL")
        log("========================================")
        
        # Check authentication
        if not self.api.access_token:
            log_error("Not authenticated - cannot sync")
            self._notify("SIMKL Sync", "Please authenticate first!")
            return self.stats
        
        # === TOAST: Import Started ===
        self._notify("SIMKL Sync", "Importing from SIMKL...")
        
        # Initialize progress dialog if requested
        if self.show_progress:
            self.progress_dialog = xbmcgui.DialogProgress()
            self.progress_dialog.create("SIMKL Sync", "Importing from SIMKL...")
        
        try:
            # Import movies
            if sync_movies:
                if self.show_progress:
                    self.progress_dialog.update(10, "Importing movies from SIMKL...")
                    if self.progress_dialog.iscanceled():
                        self.cancelled = True
                        self._notify("SIMKL Sync", "Import cancelled")
                        return self.stats
                
                self.import_movies_from_simkl()
            
            # Import episodes
            if sync_episodes:
                if self.show_progress:
                    self.progress_dialog.update(50, "Importing TV episodes from SIMKL...")
                    if self.progress_dialog.iscanceled():
                        self.cancelled = True
                        self._notify("SIMKL Sync", "Import cancelled")
                        return self.stats
                
                self.import_episodes_from_simkl()
            
            # Done!
            if self.show_progress:
                self.progress_dialog.update(100, "Import complete!")
            
        except Exception as e:
            log_error(f"Import failed with exception: {e}")
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
        
        log("========================================")
        log(f"  IMPORT COMPLETE")
        log(f"  Movies: {self.stats['movies_imported']}")
        log(f"  Episodes: {self.stats['episodes_imported']}")
        log(f"  Errors: {self.stats['errors']}")
        log("========================================")
        
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
    
    manager = SyncManager(show_progress=show_progress, silent=False)
    try:
        manager.sync_to_simkl(sync_movies=sync_movies, sync_episodes=sync_episodes)
    finally:
        manager.close()


def run_sync_from_simkl(silent=False):
    """
    Run import sync with progress dialog.
    
    Called from default.py when user triggers manual import.
    
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
    
    manager = SyncManager(show_progress=show_progress, silent=False)
    try:
        manager.sync_from_simkl(sync_movies=sync_movies, sync_episodes=sync_episodes)
    finally:
        manager.close()
