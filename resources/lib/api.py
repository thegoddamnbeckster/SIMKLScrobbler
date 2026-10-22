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
import re
import xbmc
import xbmcaddon
from resources.lib.utils import log, log_error, log_debug, log_warning, log_module_init, get_setting, set_setting
from resources.lib import oauth2

# Module version
__version__ = '7.9.27'

# Log module initialization
log_module_init('api.py', __version__)


def summarize_history_result(result):
    """
    Read a /sync/history response honestly.

    SIMKL answers a history add with HTTP 200 and {"added": {...counts}, "not_found": {...lists}} whether or not
    anything was recorded, so the response being a non-empty dict says nothing about whether the watch landed.

    Returns None when there is no usable response (the request failed), otherwise a tuple
    (added, not_found): `added` is the number of movies + episodes SIMKL recorded; `not_found` is the number of
    submitted movies/shows/episodes it could not match. added == 0 with not_found == 0 means SIMKL already had
    exactly what was sent.
    """
    if not isinstance(result, dict):
        return None
    added_block = result.get("added") or {}
    added = 0
    for key in ("movies", "episodes"):
        value = added_block.get(key, 0)
        added += value if isinstance(value, int) else 0
    not_found = 0
    for value in (result.get("not_found") or {}).values():
        if isinstance(value, list):
            not_found += len(value)
        elif isinstance(value, int):
            not_found += value
    return added, not_found


class SimklAPI:
    """SIMKL API client - talks to the SIMKL servers so you don't have to."""
    
    BASE_URL = "https://api.simkl.com"
    
    # Our registered client IDs (not secret, device auth doesn't need secrets). AUTH V2 is the one new logins use; the V1 id
    # only keeps an existing V1 token working until SIMKL retires V1 (expected around April 2027).
    CLIENT_ID = oauth2.CLIENT_ID_V2
    CLIENT_ID_V1 = oauth2.CLIENT_ID_V1
    
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
            "User-Agent": oauth2.user_agent(__version__),
        })
        # every request says which app and version it is (SIMKL requires it) and carries the client id that goes with the token
        self.session.params = oauth2.identity_params(__version__)
        self._use_client_id_for(self.access_token)
        
        # Add auth header if we have a token
        if self.access_token:
            self.session.headers.update({
                "Authorization": f"Bearer {self.access_token}"
            })
        
        # AUTH V2 tokens last 7 days and are renewed with a refresh token; the saved tokens are shared with every other
        # Kodi process of this addon, so a renewal is taken under a lock (see oauth2.refresh_access_token)
        self._tokens = oauth2.TokenStore(get_setting, set_setting, get_fresh=self._read_setting_fresh)

        log(f"[api v{__version__}] SimklAPI.__init__() SimklAPI initialized | token={token_preview} | thread={tid}")
    
    def _use_client_id_for(self, token):
        """Sends the client id that belongs to this token: V2 for a V2 token (or none yet), V1 for an existing V1 token."""
        client_id = oauth2.client_id_for(token)
        self.session.headers["simkl-api-key"] = client_id
        self.session.params["client_id"] = client_id

    @staticmethod
    def _read_setting_fresh(setting_id):
        """A setting as another process last saved it (a new Addon object, so Kodi's per-object cache is bypassed)."""
        try:
            return xbmcaddon.Addon('script.simkl.scrobbler').getSetting(setting_id)
        except Exception:
            return get_setting(setting_id)

    def _oauth_post(self, path, body):
        """POST to an /oauth2 endpoint: returns (status code, parsed JSON or None); 0 when the service cannot be reached."""
        try:
            response = requests.post(f"{self.BASE_URL}{path}", json=body, timeout=15, headers={
                "Content-Type": "application/json", "User-Agent": oauth2.user_agent(__version__)})
        except requests.exceptions.RequestException as e:
            log_error(f"[api v{__version__}] SimklAPI._oauth_post() {path} failed: {e}")
            return 0, None
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, None

    def _refresh_lock(self):
        """A lock shared by this addon's processes, kept in its profile folder (none if there is nowhere to put one)."""
        try:
            import os
            import xbmcvfs
            profile = xbmcvfs.translatePath(xbmcaddon.Addon('script.simkl.scrobbler').getAddonInfo('profile'))
            if profile and os.path.isdir(profile):
                return oauth2.FileLock(os.path.join(profile, 'token_refresh.lock'))
        except Exception as e:
            log_warning(f"[api v{__version__}] SimklAPI._refresh_lock() no lock available: {e}")
        return oauth2.NoLock()

    def ensure_fresh_token(self, force=False):
        """
        Renews an AUTH V2 access token that is expired or about to be (or any V2 token when `force`), then points this
        client at whatever token is saved. Returns the outcome of oauth2.refresh_access_token.
        """
        outcome = oauth2.refresh_access_token(self._tokens, self._oauth_post, self._refresh_lock(), force=force)
        if outcome in ("fresh", "reauth"):
            self.refresh_token()          # adopt the saved token (or its absence) into this client's headers
            if outcome == "reauth":
                log_error(f"[api v{__version__}] SimklAPI.ensure_fresh_token() SIMKL refused the refresh token - the "
                          f"account must be connected again (Settings > Authorization)")
            else:
                log(f"[api v{__version__}] SimklAPI.ensure_fresh_token() access token renewed")
        elif outcome == "failed":
            log_warning(f"[api v{__version__}] SimklAPI.ensure_fresh_token() could not renew the token now - will try again")
        return outcome

    def close(self):
        """Close the requests session to free socket connections."""
        if self.session:
            self.session.close()
            log_debug(f"[api v{__version__}] SimklAPI.close() API session closed")
    
    # ========== Retry / Backoff Configuration ==========
    # Per SIMKL team feedback (Ennergizer, 2026-02-25): the addon had no
    # rate-limiting or exponential backoff. Fixed-interval polling (auth,
    # periodic progress updates) could hammer the server during outages,
    # and 429 responses were silently dropped with no retry.
    #
    # Strategy:
    #   - Max 3 retries (4 total attempts) for retryable errors
    #   - Exponential backoff: 2s, 4s, 8s (base * 2^attempt)
    #   - 429 responses: respect Retry-After header if present, else backoff
    #   - 5xx server errors: backoff (server is struggling)
    #   - Connection errors / timeouts: backoff (network issue)
    #   - 401/404/409 and other client errors: NO retry (not transient)
    #   - Uses xbmc.sleep() so Kodi stays responsive during waits
    MAX_RETRIES = 3          # Number of retries after initial attempt
    BACKOFF_BASE = 2         # Base delay in seconds (doubles each retry)
    BACKOFF_MAX = 30         # Maximum delay cap in seconds

    def _request(self, method, endpoint, data=None, params=None, timeout=30):
        """
        Make an HTTP request to the SIMKL API with exponential backoff.
        
        Retries on transient failures (429 rate limit, 5xx server errors,
        connection errors, timeouts) with exponential backoff. Non-retryable
        errors (401, 404, 409, other 4xx) are handled immediately.
        
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
        
        # a V2 access token that is about to run out is renewed BEFORE the request, not after it fails
        if self.access_token and oauth2.is_v2_token(self.access_token):
            self.ensure_fresh_token()
            has_auth = 'Authorization' in self.session.headers
            token_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'

        log(f"[api v{__version__}] SimklAPI._request() {method} {endpoint} | thread={tid} | has_auth_header={has_auth} | token={token_preview}")
        
        if data:
            log_debug(f"[api v{__version__}] SimklAPI._request() Request body: {json.dumps(data)[:500]}")
        
        # Retry loop: attempt 0 is the initial request, attempts 1-MAX_RETRIES are retries
        for attempt in range(self.MAX_RETRIES + 1):
            if attempt > 0:
                log(f"[api v{__version__}] SimklAPI._request() Retry attempt {attempt}/{self.MAX_RETRIES} for {method} {endpoint}")
            
            try:
                # Execute the HTTP request
                response = self._execute_request(method, url, data, params, timeout)
                if response is None:
                    # _execute_request returns None for unsupported methods
                    return None
                
                log(f"[api v{__version__}] SimklAPI._request() {method} {endpoint} -> HTTP {response.status_code} | attempt={attempt} | thread={tid}")
                
                # ---- Handle non-retryable status codes first ----
                
                if response.status_code == 204:
                    # 204 No Content - success with no body
                    return {"success": True}
                
                if response.status_code == 401:
                    # 401 Unauthorized - try token refresh (not a backoff scenario)
                    return self._handle_401(method, url, endpoint, data, params, timeout, token_preview, tid)
                
                if response.status_code == 404:
                    log_error(f"[api v{__version__}] SimklAPI._request() 404 Not Found on {method} {endpoint} - content not found on SIMKL")
                    return None
                
                if response.status_code == 409:
                    # 409 Conflict - already scrobbled recently, not an error
                    log(f"[api v{__version__}] SimklAPI._request() 409 Conflict on {method} {endpoint} - already scrobbled recently")
                    try:
                        return response.json()
                    except Exception:
                        return {"conflict": True}
                
                # ---- Handle retryable status codes ----
                
                if response.status_code == 429:
                    # 429 Rate Limited - respect Retry-After header if present
                    retry_after = self._get_retry_after(response, attempt)
                    log_warning(f"[api v{__version__}] SimklAPI._request() 429 Rate Limited on {method} {endpoint} | "
                                f"attempt={attempt}/{self.MAX_RETRIES} | waiting {retry_after:.1f}s before retry")
                    
                    if attempt < self.MAX_RETRIES:
                        # Wait and retry
                        xbmc.sleep(int(retry_after * 1000))  # xbmc.sleep takes milliseconds
                        continue  # Go to next attempt
                    else:
                        # Exhausted all retries
                        log_error(f"[api v{__version__}] SimklAPI._request() 429 Rate Limited - exhausted all {self.MAX_RETRIES} retries on {method} {endpoint}")
                        return None
                
                if response.status_code >= 500:
                    # 5xx Server Error - transient, worth retrying
                    backoff_delay = self._calculate_backoff(attempt)
                    log_warning(f"[api v{__version__}] SimklAPI._request() {response.status_code} Server Error on {method} {endpoint} | "
                                f"attempt={attempt}/{self.MAX_RETRIES} | waiting {backoff_delay:.1f}s before retry")
                    
                    if attempt < self.MAX_RETRIES:
                        xbmc.sleep(int(backoff_delay * 1000))
                        continue
                    else:
                        log_error(f"[api v{__version__}] SimklAPI._request() {response.status_code} Server Error - exhausted all {self.MAX_RETRIES} retries on {method} {endpoint}")
                        return None
                
                # ---- Handle other 4xx client errors (not retryable) ----
                if 400 <= response.status_code < 500:
                    log_error(f"[api v{__version__}] SimklAPI._request() {response.status_code} Client Error on {method} {endpoint} - not retryable")
                    try:
                        error_body = response.text[:500]
                        log_debug(f"[api v{__version__}] SimklAPI._request() Error body: {error_body}")
                    except Exception:
                        pass
                    return None
                
                # ---- Success (2xx) - parse JSON response ----
                response.raise_for_status()  # Catch any other non-2xx we missed
                
                try:
                    result = response.json()
                    log_debug(f"[api v{__version__}] SimklAPI._request() Response body: {json.dumps(result)[:500]}")
                    return result
                except json.JSONDecodeError:
                    log_error(f"[api v{__version__}] SimklAPI._request() Failed to parse JSON response from {method} {endpoint}")
                    return None
                
            except requests.exceptions.Timeout:
                # Timeout - transient, worth retrying with backoff
                backoff_delay = self._calculate_backoff(attempt)
                log_error(f"[api v{__version__}] SimklAPI._request() Timeout on {method} {endpoint} | "
                          f"attempt={attempt}/{self.MAX_RETRIES} | waiting {backoff_delay:.1f}s")
                
                if attempt < self.MAX_RETRIES:
                    xbmc.sleep(int(backoff_delay * 1000))
                    continue
                else:
                    log_error(f"[api v{__version__}] SimklAPI._request() Timeout - exhausted all {self.MAX_RETRIES} retries on {method} {endpoint}")
                    return None
                    
            except requests.exceptions.ConnectionError:
                # Connection error - transient, worth retrying with backoff
                backoff_delay = self._calculate_backoff(attempt)
                log_error(f"[api v{__version__}] SimklAPI._request() Connection error on {method} {endpoint} | "
                          f"attempt={attempt}/{self.MAX_RETRIES} | waiting {backoff_delay:.1f}s")
                
                if attempt < self.MAX_RETRIES:
                    xbmc.sleep(int(backoff_delay * 1000))
                    continue
                else:
                    log_error(f"[api v{__version__}] SimklAPI._request() Connection error - exhausted all {self.MAX_RETRIES} retries on {method} {endpoint}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                # Other request errors - log and give up (likely not transient)
                log_error(f"[api v{__version__}] SimklAPI._request() Request failed on {method} {endpoint}: {e}")
                return None
        
        # Should not reach here, but safety net
        log_error(f"[api v{__version__}] SimklAPI._request() Unexpected exit from retry loop on {method} {endpoint}")
        return None
    
    def _execute_request(self, method, url, data, params, timeout):
        """
        Execute a single HTTP request. Factored out of _request() to keep
        the retry loop clean and avoid duplicating dispatch logic.
        
        Args:
            method: HTTP method string (GET, POST, DELETE)
            url: Full URL (base + endpoint)
            data: JSON body dict or None
            params: URL params dict or None
            timeout: Timeout in seconds
            
        Returns:
            requests.Response object, or None for unsupported methods
        """
        if method == "GET":
            return self.session.get(url, params=params, timeout=timeout)
        elif method == "POST":
            # params must be passed: SIMKL reads allow_rewatch from the query string, and without this the
            # parameter was silently dropped (so no rewatch could ever be recorded - 2026-10-18).
            return self.session.post(url, json=data, params=params, timeout=timeout)
        elif method == "DELETE":
            return self.session.delete(url, timeout=timeout)
        else:
            log_error(f"[api v{__version__}] SimklAPI._execute_request() Unsupported HTTP method: {method}")
            return None
    
    def _handle_401(self, method, url, endpoint, data, params, timeout, token_preview, tid):
        """
        Handle 401 Unauthorized by refreshing the token and retrying once.
        
        This is NOT part of the exponential backoff system - 401 is an auth
        issue, not a transient server problem. We try refreshing the cached
        token from Kodi's settings (in case auth completed in another process)
        and retry exactly once.
        
        Args:
            method: HTTP method
            url: Full request URL
            endpoint: API endpoint (for logging)
            data: Request body
            params: URL params
            timeout: Timeout
            token_preview: Truncated token string for logs
            tid: Thread name for logs
            
        Returns:
            Response JSON dict, or None on error
        """
        log_error(f"[api v{__version__}] SimklAPI._handle_401() 401 Unauthorized on {method} {endpoint} | token_was={token_preview} | thread={tid}")
        
        # Try refreshing token once before giving up
        old_token = self.access_token
        if oauth2.is_v2_token(self.access_token):
            # A V2 token is rejected when it has expired or another process renewed it: renew (or adopt the renewed one)
            self.ensure_fresh_token(force=True)
        log(f"[api v{__version__}] SimklAPI._handle_401() Calling refresh_token() to get latest from settings...")
        self.refresh_token()
        new_token_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'
        token_changed = self.access_token and self.access_token != old_token
        log(f"[api v{__version__}] SimklAPI._handle_401() After refresh: token={new_token_preview} | changed={token_changed}")
        
        if token_changed:
            log(f"[api v{__version__}] SimklAPI._handle_401() Retrying {method} {endpoint} with refreshed token...")
            
            try:
                response = self._execute_request(method, url, data, params, timeout)
                if response is None:
                    return None
                
                log(f"[api v{__version__}] SimklAPI._handle_401() Retry result: HTTP {response.status_code} | thread={tid}")
                
                if response.status_code == 401:
                    log_error(f"[api v{__version__}] SimklAPI._handle_401() 401 STILL after token refresh - token is truly invalid")
                    return None
                
                # Handle success
                if response.status_code == 204:
                    return {"success": True}
                
                response.raise_for_status()
                
                try:
                    return response.json()
                except json.JSONDecodeError:
                    log_error(f"[api v{__version__}] SimklAPI._handle_401() Failed to parse JSON on retry")
                    return None
                    
            except requests.exceptions.RequestException as e:
                log_error(f"[api v{__version__}] SimklAPI._handle_401() Retry request failed: {e}")
                return None
        else:
            log_error(f"[api v{__version__}] SimklAPI._handle_401() Token unchanged after refresh (was={token_preview}, now={new_token_preview}) - cannot retry")
            return None
    
    def _calculate_backoff(self, attempt):
        """
        Calculate exponential backoff delay for a given retry attempt.
        
        Formula: min(BACKOFF_BASE * 2^attempt, BACKOFF_MAX)
        
        Example with defaults (base=2, max=30):
            attempt 0 (first retry):  2 * 2^0 = 2s
            attempt 1 (second retry): 2 * 2^1 = 4s
            attempt 2 (third retry):  2 * 2^2 = 8s
        
        Args:
            attempt: Current attempt number (0-based)
            
        Returns:
            Delay in seconds (float)
        """
        delay = self.BACKOFF_BASE * (2 ** attempt)
        delay = min(delay, self.BACKOFF_MAX)
        return float(delay)
    
    def _get_retry_after(self, response, attempt):
        """
        Get the delay to wait before retrying a 429 response.
        
        Checks the Retry-After header first. If not present or not parseable,
        falls back to exponential backoff. The Retry-After header may contain
        either a number of seconds or an HTTP-date, but SIMKL typically sends
        seconds (if at all).
        
        Args:
            response: The 429 HTTP response
            attempt: Current attempt number (for backoff fallback)
            
        Returns:
            Delay in seconds (float)
        """
        retry_after = response.headers.get('Retry-After')
        
        if retry_after:
            try:
                # Try parsing as integer seconds (most common for APIs)
                delay = float(retry_after)
                # Sanity check - clamp to backoff_floor-60 second range.
                # A zero/negative Retry-After from a broken server or load
                # balancer would cause a tight retry loop, so we floor at the
                # exponential backoff value to guarantee meaningful wait time.
                backoff_floor = self._calculate_backoff(attempt)
                delay = max(backoff_floor, min(delay, 60.0))
                log(f"[api v{__version__}] SimklAPI._get_retry_after() Using Retry-After header: {delay:.1f}s")
                return delay
            except (ValueError, TypeError):
                log_warning(f"[api v{__version__}] SimklAPI._get_retry_after() Could not parse Retry-After header: '{retry_after}' - using exponential backoff")
        
        # No Retry-After header or couldn't parse it - use exponential backoff
        return self._calculate_backoff(attempt)
    
    # ========== Scrobbling Endpoints ==========
    
    def scrobble(self, action, movie=None, show=None, episode=None, anime=None, progress=0, allow_rewatch=False):
        """
        Send a scrobble action to SIMKL.
        
        Args:
            action: "start", "pause", or "stop"
            movie: Movie object (for movie scrobbles)
            show: Show object (for TV scrobbles)
            episode: Episode object (for TV scrobbles)
            anime: Anime object (for anime scrobbles)
            progress: Current watch progress (0-100)
            allow_rewatch: Send ?allow_rewatch=yes on a "stop" so SIMKL can record a rewatch (Pro/VIP; SIMKL
                           decides whether this stop is a first watch, a rewatch, or too soon after the last one
                           and says which in the response's rewatch_status). Never sent on start or pause.

        Returns:
            Response dict or None
        """
        valid_actions= ["start", "pause", "stop"]
        if action not in valid_actions:
            log_error(f"[api v{__version__}] SimklAPI.scrobble() Invalid scrobble action: {action}")
            return None
        
        endpoint = f"/scrobble/{action}"
        
        # Build request body
        data = {
            "progress": progress
        }
        
        # Add the appropriate media object
        if movie:
            data["movie"] = movie
            log(f"[api v{__version__}] SimklAPI.scrobble() Scrobble {action}: Movie - {movie.get('title', 'Unknown')}")
        elif show and episode:
            data["show"] = show
            data["episode"] = episode
            show_title = show.get("title", "Unknown")
            season = episode.get("season", 0)
            ep_num = episode.get("number", episode.get("episode", 0))
            log(f"[api v{__version__}] SimklAPI.scrobble() Scrobble {action}: {show_title} S{season:02d}E{ep_num:02d}")
        elif anime and episode:
            data["anime"] = anime
            data["episode"] = episode
            log(f"[api v{__version__}] SimklAPI.scrobble() Scrobble {action}: Anime - {anime.get('title', 'Unknown')}")
        else:
            log_error(f"[api v{__version__}] SimklAPI.scrobble() Scrobble requires movie, show+episode, or anime+episode")
            return None
        
        params = {"allow_rewatch": "yes"} if (allow_rewatch and action == "stop") else None
        return self._request("POST", endpoint, data=data, params=params)

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
        
        log(f"[api v{__version__}] SimklAPI.search_movie() Searching movies: {query}" + (f" ({year})" if year else ""))
        
        response = self._request("GET", "/search/movie", params=params)
        
        if response and isinstance(response, list):
            log(f"[api v{__version__}] SimklAPI.search_movie() Found {len(response)} movie result(s)")
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
        
        log(f"[api v{__version__}] SimklAPI.search_tv() Searching TV shows: {query}" + (f" ({year})" if year else ""))
        
        response = self._request("GET", "/search/tv", params=params)
        
        if response and isinstance(response, list):
            log(f"[api v{__version__}] SimklAPI.search_tv() Found {len(response)} TV show result(s)")
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
        
        log(f"[api v{__version__}] SimklAPI.search_anime() Searching anime: {query}" + (f" ({year})" if year else ""))
        
        response = self._request("GET", "/search/anime", params=params)
        
        if response and isinstance(response, list):
            log(f"[api v{__version__}] SimklAPI.search_anime() Found {len(response)} anime result(s)")
            return response
        
        return []
    
    # ========== Sync Endpoints ==========
    
    def add_to_history(self, movies=None, shows=None, allow_rewatch=False):
        """
        Add items directly to watch history.
        
        Used for manual marking without scrobble flow.
        Watch history is stored permanently in SIMKL's cloud.
        
        Args:
            movies: List of movie objects with format:
                    {"title": "...", "year": 2020, "ids": {"imdb": "tt..."}}
            shows: List of show objects with episodes:
                   {"title": "...", "ids": {...}, "seasons": [{"number": 1, "episodes": [{"number": 1}]}]}
            allow_rewatch: Send ?allow_rewatch=yes so an item SIMKL already has as watched is recorded as a
                           rewatch session when it carries a watched_at at least two days from the last watch
                           (Pro/VIP; on a free account SIMKL answers 200 and adds nothing). Without it a watch of an
                           already-watched item is a silent no-op. Only pass it for a watch that really is a new one:
                           with it, re-sending an old entry whose time differs from SIMKL's can create a rewatch.

        Returns:
            Response dict with 'added' counts or None on error
        """
        data = {}
        if movies:
            data["movies"] = movies
        if shows:
            data["shows"] = shows
        
        if not data:
            log_error(f"[api v{__version__}] SimklAPI.add_to_history() No items provided to add to history")
            return None
        
        movie_count = len(data.get('movies', []))
        show_count = len(data.get('shows', []))
        
        log(f"[api v{__version__}] SimklAPI.add_to_history() Adding to history: {movie_count} movies, {show_count} shows")
        log_debug(f"[api v{__version__}] SimklAPI.add_to_history() History payload: {json.dumps(data)[:500]}...")  # First 500 chars
        
        params = {"allow_rewatch": "yes"} if allow_rewatch else None
        result = self._request("POST", "/sync/history", data=data, params=params)

        summary = summarize_history_result(result)
        if summary is not None:
            added, not_found = summary
            if not_found:
                log_warning(f"[api v{__version__}] SimklAPI.add_to_history() SIMKL could not match {not_found} "
                            f"submitted item(s) - nothing recorded for them: {json.dumps(result.get('not_found'))[:300]}")
            else:
                log(f"[api v{__version__}] SimklAPI.add_to_history() SIMKL recorded {added} watch(es)"
                    f"{'' if added else ' (it already had exactly what was sent)'}")
        return result

    def get_all_items(self, media_type="movies", status="completed", extended=False, date_from=None,
                      episode_watched_at=False):
        """
        Get items from user's SIMKL watchlist, optionally filtered by date.
        
        Retrieves the user's watched items from SIMKL.
        Used for importing watch history FROM SIMKL to Kodi.
        
        Per SIMKL team feedback (Ennergizer, 2026-02-25): use the date_from
        parameter for incremental sync instead of fetching ALL items every time.
        The recommended flow is:
          1. First sync: call without date_from (full fetch)
          2. Before subsequent syncs: call get_last_activity() to check timestamps
          3. If activity timestamps changed: call with date_from=last_sync_timestamp
          4. Store new timestamps after successful sync
        
        Args:
            media_type: "movies", "shows", or "anime"
            status: "completed", "watching", "plantowatch", "hold", "dropped"
            extended: Include extended info (runtime, genres, etc.)
            episode_watched_at: Ask SIMKL to include each episode's own real
                       "watched_at" time in the seasons[] array (shows only;
                       it has no effect without extended). Without it episodes
                       carry just their number.
            date_from: ISO 8601 timestamp string (e.g. "2026-01-15T00:00:00Z").
                       If provided, only returns items added/changed after this date.
                       If None, returns ALL items (full sync).
            
        Returns:
            List of items or empty list on error
        """
        endpoint = f"/sync/all-items/{media_type}/{status}"
        
        params = {}
        if extended:
            params["extended"] = "full"
            if episode_watched_at:
                params["episode_watched_at"] = "yes"

        # date_from parameter enables incremental sync - only fetch items
        # that changed since the given timestamp, dramatically reducing
        # API payload size and server load for subsequent syncs
        if date_from:
            # Validate ISO 8601 format before passing to API.
            # Protects against corrupted settings data being sent as a query param.
            # Expected format: "2026-02-26T10:00:00Z" or similar ISO 8601 variants
            if re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', str(date_from)):
                params["date_from"] = date_from
                log(f"[api v{__version__}] SimklAPI.get_all_items() Fetching {status} {media_type} from SIMKL (incremental, date_from={date_from})")
            else:
                log_warning(f"[api v{__version__}] SimklAPI.get_all_items() Invalid date_from format: '{date_from}' - ignoring, doing full fetch")
                log(f"[api v{__version__}] SimklAPI.get_all_items() Fetching {status} {media_type} from SIMKL (FULL fetch, invalid date_from discarded)")
        else:
            log(f"[api v{__version__}] SimklAPI.get_all_items() Fetching {status} {media_type} from SIMKL (FULL fetch, no date_from)")
        
        response = self._request("GET", endpoint, params=params if params else None)
        
        if response and isinstance(response, list):
            log(f"[api v{__version__}] SimklAPI.get_all_items() Retrieved {len(response)} {media_type} from SIMKL")
            return response
        elif response and isinstance(response, dict) and media_type in response:
            # Some endpoints return {"movies": [...]} format
            items = response[media_type]
            log(f"[api v{__version__}] SimklAPI.get_all_items() Retrieved {len(items)} {media_type} from SIMKL")
            return items
        
        log_warning(f"[api v{__version__}] SimklAPI.get_all_items() No {status} {media_type} found on SIMKL")
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
            log_error(f"[api v{__version__}] SimklAPI.add_rating() Invalid rating: {rating} (must be 1-10)")
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
            log(f"[api v{__version__}] SimklAPI.add_rating() Rating movie: {media_info.get('title', 'Unknown')} - {rating}/10")
            
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
            log(f"[api v{__version__}] SimklAPI.add_rating() Rating show: {media_info.get('title', 'Unknown')} - {rating}/10")
            
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
            log(f"[api v{__version__}] SimklAPI.add_rating() Rating episode: {media_info.get('show_title', 'Unknown')} "
                f"S{media_info.get('season', 0):02d}E{media_info.get('episode', 0):02d} - {rating}/10")
        
        else:
            log_error(f"[api v{__version__}] SimklAPI.add_rating() Unknown media type for rating: {media_type}")
            return None
        
        log_debug(f"[api v{__version__}] SimklAPI.add_rating() Rating payload: {json.dumps(data)}")
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
            log_error(f"[api v{__version__}] SimklAPI.remove_rating() Unknown media type for unrating: {media_type}")
            return None
        
        log(f"[api v{__version__}] SimklAPI.remove_rating() Removing rating for: {media_info.get('title', 'Unknown')}")
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
        
        log(f"[api v{__version__}] SimklAPI.get_user_ratings() Fetching user ratings for {media_type}...")
        response = self._request("GET", endpoint)
        
        if response is None:
            return []
        
        if isinstance(response, dict):
            # SIMKL returns ratings nested: {"movies": [...]} or {"shows": [...]}
            for key in [media_type, 'ratings', 'movies', 'shows']:
                if key in response and isinstance(response[key], list):
                    log(f"[api v{__version__}] SimklAPI.get_user_ratings() Retrieved {len(response[key])} {media_type} ratings")
                    return response[key]
            log(f"[api v{__version__}] SimklAPI.get_user_ratings() Dict response but no recognized list key: {list(response.keys())}")
            return []
        elif isinstance(response, list):
            log(f"[api v{__version__}] SimklAPI.get_user_ratings() Retrieved {len(response)} {media_type} ratings")
            return response
        
        log_warning(f"[api v{__version__}] SimklAPI.get_user_ratings() Unexpected response type: {type(response).__name__}")
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
        return oauth2.request_device_code(self._oauth_post)
    
    def check_device_auth(self, user_code):
        """
        Check if user has authorized the device code.
        
        Args:
            user_code: The code user enters on SIMKL website
            
        Returns:
            Dict with access_token if authorized, or status info
        """
        return oauth2.poll_device_token(self._oauth_post, user_code)
    
    # ========== Utility Methods ==========
    
    def test_connection(self):
        """
        Test if we can connect to SIMKL API.
        
        Returns:
            True if connection successful
        """
        log(f"[api v{__version__}] SimklAPI.test_connection() Testing SIMKL API connection...")
        
        # Try to get user settings (requires auth)
        if self.access_token:
            result = self.get_user_settings()
            if result:
                log(f"[api v{__version__}] SimklAPI.test_connection() API connection test successful (authenticated)")
                return True
            else:
                log_error(f"[api v{__version__}] SimklAPI.test_connection() API connection test failed (auth error?)")
                return False
        else:
            # Try a simple search (no auth required)
            result = self.search_movie("test", 2020)
            if result is not None:  # Empty list is ok
                log(f"[api v{__version__}] SimklAPI.test_connection() API connection test successful (unauthenticated)")
                return True
            else:
                log_error(f"[api v{__version__}] SimklAPI.test_connection() API connection test failed")
                return False
    
    def refresh_token(self):
        """
        Refresh the access token from addon settings.
        
        Creates a FRESH Addon() instance to bypass any settings cache.
        """
        import threading
        tid = threading.current_thread().name
        log(f"[api v{__version__}] SimklAPI.refresh_token() called | thread={tid}")
        
        try:
            import xbmcaddon
            fresh_addon = xbmcaddon.Addon('script.simkl.scrobbler')
            new_token = fresh_addon.getSetting('access_token')
            log(f"[api v{__version__}] SimklAPI.refresh_token() fresh Addon read: token={'YES (len=' + str(len(new_token)) + ')' if new_token else 'EMPTY'}")
        except Exception as e:
            log_error(f"[api v{__version__}] SimklAPI.refresh_token() fresh Addon failed: {e} - falling back to cached")
            new_token = get_setting("access_token")
        
        if new_token != self.access_token:
            old_preview = self.access_token[:8] + '...' if self.access_token else 'NONE'
            new_preview = new_token[:8] + '...' if new_token else 'NONE'
            self.access_token = new_token
            self._use_client_id_for(self.access_token)
            if self.access_token:
                self.session.headers.update({
                    "Authorization": f"Bearer {self.access_token}"
                })
                log(f"[api v{__version__}] SimklAPI.refresh_token() token CHANGED: {old_preview} -> {new_preview}")
            else:
                self.session.headers.pop("Authorization", None)
                log(f"[api v{__version__}] SimklAPI.refresh_token() token CLEARED (was {old_preview})")
        else:
            log(f"[api v{__version__}] SimklAPI.refresh_token() token unchanged")
