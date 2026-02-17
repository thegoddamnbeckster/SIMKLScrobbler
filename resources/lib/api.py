# -*- coding: utf-8 -*-
"""
SIMKL API Client
Version: 7.4.4
Last Modified: 2026-02-17

PHASE 9: Advanced Features & Polish

Handles all communication with the SIMKL API.
This module manages all HTTP requests to SIMKL endpoints.

API Documentation: https://simkl.docs.apiary.io/

Key Endpoints Used:
- /oauth/pin - Device authentication
- /oauth/pin/{code} - Check auth status
- /scrobble/start - Start watching
- /scrobble/pause - Pause watching
- /scrobble/stop - Stop watching (marks as watched if 80%+)
- /search/movie - Search movies
- /search/tv - Search TV shows
- /sync/history - Add to watch history manually

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import requests
import json
import xbmc
import xbmcaddon
from resources.lib.utils import log, log_error, log_debug, log_warning, log_module_init, get_setting

# Module version
__version__ = '7.4.4'

# Log module initialization
log_module_init('api.py', __version__)


class SimklAPI:
    """SIMKL API client - talks to the SIMKL servers so you don't have to."""
    
    BASE_URL = "https://api.simkl.com"
    
    # Our registered client ID (not secret, device auth doesn't need secrets)
    CLIENT_ID = "ab02f10030b0d629ffada90e2bf6236c57f42256a9e94d243255392af7b391e7"
    
    def __init__(self):
        """
        Initialize API client.
        
        Sets up the session with required headers.
        """
        import threading
        tid = threading.current_thread().name
        self.access_token = get_setting("access_token")
        token_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'
        
        # Create persistent session for connection reuse
        self.session = requests.Session()
        
        # Set default headers
        self.session.headers.update({
            "Content-Type": "application/json",
            "simkl-api-key": self.CLIENT_ID
        })
        
        # Add auth header if we have a token
        if self.access_token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}"
            })
        
        log(f"[api v7.4.4] SimklAPI.__init__() SimklAPI initialized | token={token_preview} | thread={tid}")
    
    def close(self):
        """Close the requests session to free socket connections."""
        if self.session:
            self.session.close()
            log_debug("[api v7.4.4] SimklAPI.close() API session closed")
    
    def _request(self, method, endpoint, data=None, params=None, timeout=30):
        """
        Make an HTTP request to the SIMKL API.
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint (without base URL)
            data: Dict to send as JSON body (for POST)
            params: URL parameters (for GET)
            timeout: Request timeout in seconds
            
        Returns:
            Response JSON dict, or None on error
        """
        import threading
        tid = threading.current_thread().name
        url = f"{self.BASE_URL}{endpoint}"
        has_auth = 'Authorization' in self.session.headers
        token_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'
        
        log(f"[api v7.4.4] SimklAPI._request() {method} {endpoint} | thread={tid} | has_auth_header={has_auth} | token={token_preview}")
        
        try:
            if data:
                log_debug(f"[api v7.4.4] SimklAPI._request() Request body: {json.dumps(data)[:500]}")
            
            if method == "GET":
                response = self.session.get(url, params=params, timeout=timeout)
            elif method == "POST":
                response = self.session.post(url, json=data, timeout=timeout)
            elif method == "DELETE":
                response = self.session.delete(url, timeout=timeout)
            else:
                log_error(f"[api v7.4.4] SimklAPI._request() Unsupported HTTP method: {method}")
                return None
            
            log(f"[api v7.4.4] SimklAPI._request() {method} {endpoint} -> HTTP {response.status_code} | thread={tid}")
            
            # Handle specific status codes
            if response.status_code == 204:
                # No content - success
                return {"success": True}
            
            if response.status_code == 401:
                log_error(f"[api v7.4.4] SimklAPI._request() 401 Unauthorized on {method} {endpoint} | token_was={token_preview} | thread={tid}")
                # Try refreshing token once before giving up
                old_token = self.access_token
                log(f"[api v7.4.4] SimklAPI._request() Calling refresh_token() to get latest from settings...")
                self.refresh_token()
                new_token_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'
                token_changed = self.access_token and self.access_token != old_token
                log(f"[api v7.4.4] SimklAPI._request() After refresh: token={new_token_preview} | changed={token_changed}")
                
                if token_changed:
                    log(f"[api v7.4.4] SimklAPI._request() Retrying {method} {endpoint} with refreshed token...")
                    # Retry the request with new token
                    if method == "GET":
                        response = self.session.get(url, params=params, timeout=timeout)
                    elif method == "POST":
                        response = self.session.post(url, json=data, timeout=timeout)
                    elif method == "DELETE":
                        response = self.session.delete(url, timeout=timeout)
                    
                    log(f"[api v7.4.4] SimklAPI._request() Retry result: HTTP {response.status_code} | thread={tid}")
                    
                    if response.status_code == 401:
                        log_error(f"[api v7.4.4] SimklAPI._request() 401 STILL after token refresh - token is truly invalid")
                        return None
                    # Fall through to normal response handling
                else:
                    log_error(f"[api v7.4.4] SimklAPI._request() Token unchanged after refresh (was={token_preview}, now={new_token_preview}) - cannot retry")
                    return None
            
            if response.status_code == 404:
                log_error("[api v7.4.4] SimklAPI._request() 404 Not Found - content not found on SIMKL")
                return None
            
            if response.status_code == 409:
                # Conflict - already watched recently
                log("[api v7.4.4] SimklAPI._request() 409 Conflict - already scrobbled recently")
                try:
                    return response.json()
                except:
                    return {"conflict": True}
            
            if response.status_code == 429:
                log_error("[api v7.4.4] SimklAPI._request() 429 Rate Limited - slow down!")
                return None
            
            response.raise_for_status()
            
            # Parse JSON response
            try:
                result = response.json()
                log_debug(f"[api v7.4.4] SimklAPI._request() Response body: {json.dumps(result)}")
                return result
            except json.JSONDecodeError:
                log_error("[api v7.4.4] SimklAPI._request() Failed to parse JSON response")
                return None
                
        except requests.exceptions.Timeout:
            log_error(f"[api v7.4.4] SimklAPI._request() Request timeout: {endpoint}")
            return None
        except requests.exceptions.ConnectionError:
            log_error(f"[api v7.4.4] SimklAPI._request() Connection error: {endpoint}")
            return None
        except requests.exceptions.RequestException as e:
            log_error(f"[api v7.4.4] SimklAPI._request() Request failed: {endpoint} - {e}")
            return None
    
    # ========== Scrobbling Endpoints ==========
    
    def scrobble(self, action, movie=None, show=None, episode=None, anime=None, progress=0):
        """
        Send a scrobble action to SIMKL.
        
        Args:
            action: "start", "pause", or "stop"
            movie: Movie object (for movie scrobbles)
            show: Show object (for TV scrobbles)
            episode: Episode object (for TV scrobbles)
            anime: Anime object (for anime scrobbles)
            progress: Current watch progress (0-100)
            
        Returns:
            Response dict or None
        """
        valid_actions = ["start", "pause", "stop"]
        if action not in valid_actions:
            log_error(f"[api v7.4.4] SimklAPI.scrobble() Invalid scrobble action: {action}")
            return None
        
        endpoint = f"/scrobble/{action}"
        
        # Build request body
        data = {
            "progress": progress
        }
        
        # Add the appropriate media object
        if movie:
            data["movie"] = movie
            log(f"[api v7.4.4] SimklAPI.scrobble() Scrobble {action}: Movie - {movie.get('title', 'Unknown')}")
        elif show and episode:
            data["show"] = show
            data["episode"] = episode
            show_title = show.get("title", "Unknown")
            season = episode.get("season", 0)
            ep_num = episode.get("number", episode.get("episode", 0))
            log(f"[api v7.4.4] SimklAPI.scrobble() Scrobble {action}: {show_title} S{season:02d}E{ep_num:02d}")
        elif anime and episode:
            data["anime"] = anime
            data["episode"] = episode
            log(f"[api v7.4.4] SimklAPI.scrobble() Scrobble {action}: Anime - {anime.get('title', 'Unknown')}")
        else:
            log_error("[api v7.4.4] SimklAPI.scrobble() Scrobble requires movie, show+episode, or anime+episode")
            return None
        
        return self._request("POST", endpoint, data=data)
    
    # ========== Search Endpoints ==========
    
    def search_movie(self, query, year=None):
        """
        Search for a movie on SIMKL.
        
        Args:
            query: Search query (movie title)
            year: Release year (optional but recommended)
            
        Returns:
            List of matching movies or empty list
        """
        params = {"q": query}
        if year:
            params["year"] = year
        
        log(f"[api v7.4.4] SimklAPI.search_movie() Searching movies: {query}" + (f" ({year})" if year else ""))
        
        response = self._request("GET", "/search/movie", params=params)
        
        if response and isinstance(response, list):
            log(f"[api v7.4.4] SimklAPI.search_movie() Found {len(response)} movie result(s)")
            return response
        
        return []
    
    def search_tv(self, query, year=None):
        """
        Search for a TV show on SIMKL.
        
        Args:
            query: Search query (show title)
            year: First air year (optional)
            
        Returns:
            List of matching shows or empty list
        """
        params = {"q": query}
        if year:
            params["year"] = year
        
        log(f"[api v7.4.4] SimklAPI.search_tv() Searching TV shows: {query}" + (f" ({year})" if year else ""))
        
        response = self._request("GET", "/search/tv", params=params)
        
        if response and isinstance(response, list):
            log(f"[api v7.4.4] SimklAPI.search_tv() Found {len(response)} TV show result(s)")
            return response
        
        return []
    
    def search_anime(self, query, year=None):
        """
        Search for anime on SIMKL.
        
        Args:
            query: Search query (anime title)
            year: Year (optional)
            
        Returns:
            List of matching anime or empty list
        """
        params = {"q": query}
        if year:
            params["year"] = year
        
        log(f"[api v7.4.4] SimklAPI.search_anime() Searching anime: {query}" + (f" ({year})" if year else ""))
        
        response = self._request("GET", "/search/anime", params=params)
        
        if response and isinstance(response, list):
            log(f"[api v7.4.4] SimklAPI.search_anime() Found {len(response)} anime result(s)")
            return response
        
        return []
    
    # ========== Sync Endpoints ==========
    
    def add_to_history(self, movies=None, shows=None):
        """
        Add items directly to watch history.
        
        Used for manual marking without scrobble flow.
        Watch history is stored permanently in SIMKL's cloud.
        
        Args:
            movies: List of movie objects with format:
                    {"title": "...", "year": 2020, "ids": {"imdb": "tt..."}}
            shows: List of show objects with episodes:
                   {"title": "...", "ids": {...}, "seasons": [{"number": 1, "episodes": [{"number": 1}]}]}
            
        Returns:
            Response dict with 'added' counts or None on error
        """
        data = {}
        if movies:
            data["movies"] = movies
        if shows:
            data["shows"] = shows
        
        if not data:
            log_error("[api v7.4.4] SimklAPI.add_to_history() No items provided to add to history")
            return None
        
        movie_count = len(data.get('movies', []))
        show_count = len(data.get('shows', []))
        
        log(f"[api v7.4.4] SimklAPI.add_to_history() Adding to history: {movie_count} movies, {show_count} shows")
        log_debug(f"[api v7.4.4] SimklAPI.add_to_history() History payload: {json.dumps(data)[:500]}...")  # First 500 chars
        
        return self._request("POST", "/sync/history", data=data)
    
    def get_all_items(self, media_type="movies", status="completed", extended=False):
        """
        Get all items from user's SIMKL watchlist.
        
        Retrieves the user's watched items from SIMKL.
        Used for importing watch history FROM SIMKL to Kodi.
        
        Args:
            media_type: "movies", "shows", or "anime"
            status: "completed", "watching", "plantowatch", "hold", "dropped"
            extended: Include extended info (runtime, genres, etc.)
            
        Returns:
            List of items or empty list on error
        """
        endpoint = f"/sync/all-items/{media_type}/{status}"
        
        params = {}
        if extended:
            params["extended"] = "full"
        
        log(f"[api v7.4.4] SimklAPI.get_all_items() Fetching {status} {media_type} from SIMKL...")
        
        response = self._request("GET", endpoint, params=params if params else None)
        
        if response and isinstance(response, list):
            log(f"[api v7.4.4] SimklAPI.get_all_items() Retrieved {len(response)} {media_type} from SIMKL")
            return response
        elif response and isinstance(response, dict) and media_type in response:
            # Some endpoints return {"movies": [...]} format
            items = response[media_type]
            log(f"[api v7.4.4] SimklAPI.get_all_items() Retrieved {len(items)} {media_type} from SIMKL")
            return items
        
        log_warning(f"[api v7.4.4] SimklAPI.get_all_items() No {status} {media_type} found on SIMKL")
        return []
    
    def get_last_activity(self):
        """
        Get timestamps of user's last activities.
        
        Useful for determining if a sync is needed.
        
        Returns:
            Dict with activity timestamps or None
        """
        return self._request("GET", "/sync/activities")
    
    # ========== Rating Endpoints ==========
    
    def add_rating(self, media_type, media_info, rating):
        """
        Add a rating for a movie, show, or episode.
        
        SIMKL uses 1-10 rating scale.
        Rating an item also adds it to watched history if not already there.
        
        Args:
            media_type: "movie", "show", or "episode"
            media_info: Dict with ids and metadata
            rating: Integer 1-10
            
        Returns:
            Response dict or None on error
        """
        if not 1 <= rating <= 10:
            log_error(f"[api v7.4.4] SimklAPI.add_rating() Invalid rating: {rating} (must be 1-10)")
            return None
        
        data = {}
        
        if media_type == "movie":
            movie_obj = {
                "rating": rating,
                "ids": media_info.get("ids", {}),
            }
            # Add optional fields
            if "title" in media_info:
                movie_obj["title"] = media_info["title"]
            if "year" in media_info:
                movie_obj["year"] = media_info["year"]
            
            data["movies"] = [movie_obj]
            log(f"[api v7.4.4] SimklAPI.add_rating() Rating movie: {media_info.get('title', 'Unknown')} - {rating}/10")
            
        elif media_type == "show":
            # For shows, rate the show itself (not individual episodes)
            show_obj = {
                "rating": rating,
                "ids": media_info.get("ids", {}),
            }
            if "title" in media_info:
                show_obj["title"] = media_info["title"]
            if "year" in media_info:
                show_obj["year"] = media_info["year"]
            
            data["shows"] = [show_obj]
            log(f"[api v7.4.4] SimklAPI.add_rating() Rating show: {media_info.get('title', 'Unknown')} - {rating}/10")
            
        elif media_type == "episode":
            # For episodes, we need to structure it as show -> season -> episode
            show_obj = {
                "ids": media_info.get("show_ids", media_info.get("ids", {})),
                "seasons": [{
                    "number": media_info.get("season", 1),
                    "episodes": [{
                        "number": media_info.get("episode", 1),
                        "rating": rating
                    }]
                }]
            }
            if "show_title" in media_info:
                show_obj["title"] = media_info["show_title"]
            
            data["shows"] = [show_obj]
            log(f"[api v7.4.4] SimklAPI.add_rating() Rating episode: {media_info.get('show_title', 'Unknown')} "
                f"S{media_info.get('season', 0):02d}E{media_info.get('episode', 0):02d} - {rating}/10")
        
        else:
            log_error(f"[api v7.4.4] SimklAPI.add_rating() Unknown media type for rating: {media_type}")
            return None
        
        log_debug(f"[api v7.4.4] SimklAPI.add_rating() Rating payload: {json.dumps(data)}")
        return self._request("POST", "/sync/ratings", data=data)
    
    def remove_rating(self, media_type, media_info):
        """
        Remove a rating from an item.
        
        Args:
            media_type: "movie", "show", or "episode"
            media_info: Dict with ids
            
        Returns:
            Response dict or None on error
        """
        data = {}
        
        if media_type == "movie":
            data["movies"] = [{
                "ids": media_info.get("ids", {})
            }]
        elif media_type == "show":
            data["shows"] = [{
                "ids": media_info.get("ids", {})
            }]
        elif media_type == "episode":
            data["shows"] = [{
                "ids": media_info.get("show_ids", media_info.get("ids", {})),
                "seasons": [{
                    "number": media_info.get("season", 1),
                    "episodes": [{
                        "number": media_info.get("episode", 1)
                    }]
                }]
            }]
        else:
            log_error(f"[api v7.4.4] SimklAPI.remove_rating() Unknown media type for unrating: {media_type}")
            return None
        
        log(f"[api v7.4.4] SimklAPI.remove_rating() Removing rating for: {media_info.get('title', 'Unknown')}")
        return self._request("POST", "/sync/ratings/remove", data=data)
    
    def get_user_ratings(self, media_type="movies"):
        """
        Get user's ratings from SIMKL.
        
        Args:
            media_type: "movies", "shows", or "anime"
            
        Returns:
            List of rated items or empty list
        """
        endpoint = f"/sync/ratings/{media_type}"
        
        log(f"[api v7.4.4] SimklAPI.get_user_ratings() Fetching user ratings for {media_type}...")
        response = self._request("GET", endpoint)
        
        if response is None:
            return []
        
        if isinstance(response, dict):
            # SIMKL returns ratings nested: {"movies": [...]} or {"shows": [...]}
            for key in [media_type, 'ratings', 'movies', 'shows']:
                if key in response and isinstance(response[key], list):
                    log(f"[api v7.4.4] SimklAPI.get_user_ratings() Retrieved {len(response[key])} {media_type} ratings")
                    return response[key]
            log(f"[api v7.4.4] SimklAPI.get_user_ratings() Dict response but no recognized list key: {list(response.keys())}")
            return []
        elif isinstance(response, list):
            log(f"[api v7.4.4] SimklAPI.get_user_ratings() Retrieved {len(response)} {media_type} ratings")
            return response
        
        log_warning(f"[api v7.4.4] SimklAPI.get_user_ratings() Unexpected response type: {type(response).__name__}")
        return []
    
    def get_ratings(self, media_type="movies"):
        """
        Alias for get_user_ratings() - used by rating.py
        
        Args:
            media_type: "movies", "shows", or "anime"
            
        Returns:
            List of rated items or empty list
        """
        return self.get_user_ratings(media_type)
    
    # ========== User Endpoints ==========
    
    def get_user_settings(self):
        """
        Get current user's settings.
        
        Useful for testing if auth is working.
        
        Returns:
            User settings dict or None
        """
        return self._request("GET", "/users/settings")
    
    def get_user_info(self):
        """
        Get current user's info.
        
        Returns:
            User info dict or None
        """
        return self._request("GET", "/users/me")
    
    # ========== Playback Endpoints ==========
    
    def get_playback(self, media_type="all"):
        """
        Get current playback sessions (paused items).
        
        Args:
            media_type: "all", "movie", or "episode"
            
        Returns:
            List of playback sessions or empty list
        """
        endpoint = "/sync/playback"
        if media_type in ["movie", "episode"]:
            endpoint = f"/sync/playback/{media_type}"
        
        response = self._request("GET", endpoint)
        
        if response and isinstance(response, list):
            return response
        
        return []
    
    # ========== Auth Endpoints (used by auth_dialog.py) ==========
    
    def get_device_code(self):
        """
        Get device code for PIN-based authentication.
        
        Returns:
            Dict with device_code, user_code, verification_url
        """
        params = {"client_id": self.CLIENT_ID}
        return self._request("GET", "/oauth/pin", params=params)
    
    def check_device_auth(self, user_code):
        """
        Check if user has authorized the device code.
        
        Args:
            user_code: The code user enters on SIMKL website
            
        Returns:
            Dict with access_token if authorized, or status info
        """
        params = {"client_id": self.CLIENT_ID}
        return self._request("GET", f"/oauth/pin/{user_code}", params=params)
    
    # ========== Utility Methods ==========
    
    def test_connection(self):
        """
        Test if we can connect to SIMKL API.
        
        Returns:
            True if connection successful
        """
        log("[api v7.4.4] SimklAPI.test_connection() Testing SIMKL API connection...")
        
        # Try to get user settings (requires auth)
        if self.access_token:
            result = self.get_user_settings()
            if result:
                log("[api v7.4.4] SimklAPI.test_connection() API connection test successful (authenticated)")
                return True
            else:
                log_error("[api v7.4.4] SimklAPI.test_connection() API connection test failed (auth error?)")
                return False
        else:
            # Try a simple search (no auth required)
            result = self.search_movie("test", 2020)
            if result is not None:  # Empty list is ok
                log("[api v7.4.4] SimklAPI.test_connection() API connection test successful (unauthenticated)")
                return True
            else:
                log_error("[api v7.4.4] SimklAPI.test_connection() API connection test failed")
                return False
    
    def refresh_token(self):
        """
        Refresh the access token from addon settings.
        
        Creates a FRESH Addon() instance to bypass any settings cache.
        """
        import threading
        tid = threading.current_thread().name
        log(f"[api v7.4.4] SimklAPI.refresh_token() called | thread={tid}")
        
        try:
            import xbmcaddon
            fresh_addon = xbmcaddon.Addon('script.simkl.scrobbler')
            new_token = fresh_addon.getSetting('access_token')
            log(f"[api v7.4.4] SimklAPI.refresh_token() fresh Addon read: token={'YES (len=' + str(len(new_token)) + ')' if new_token else 'EMPTY'}")
        except Exception as e:
            log_error(f"[api v7.4.4] SimklAPI.refresh_token() fresh Addon failed: {e} - falling back to cached")
            new_token = get_setting("access_token")
        
        if new_token != self.access_token:
            old_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'
            new_preview = new_token[:8] + '...' if new_token else 'NONE'
            self.access_token = new_token
            if self.access_token:
                self.session.headers.update({
                    "Authorization": f"Bearer {self.access_token}"
                })
                log(f"[api v7.4.4] SimklAPI.refresh_token() token CHANGED: {old_preview} -> {new_preview}")
            else:
                self.session.headers.pop("Authorization", None)
                log(f"[api v7.4.4] SimklAPI.refresh_token() token CLEARED (was {old_preview})")
        else:
            log(f"[api v7.4.4] SimklAPI.refresh_token() token unchanged")
