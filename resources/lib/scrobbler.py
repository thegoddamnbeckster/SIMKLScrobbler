# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - The Brains of the Operation
Version: 7.3.0
Last Modified: 2026-02-06

PHASE 9: Advanced Features & Polish

This class handles all the scrobbling logic:
- Identifying what's playing
- Tracking watch progress
- Deciding when to mark as watched
- Communicating with SIMKL API
- Prompting for ratings after watching

The core scrobbling engine that processes playback events
and sends data to SIMKL at the appropriate times.

Professional code - Project 4 standards
"""

import xbmc
import time
import math
from resources.lib.utils import (
    log, log_error, log_debug, log_warning,
    get_setting, get_setting_bool, get_setting_int, get_setting_float, notify
)
from resources.lib.exclusions import check_exclusion, get_exclusion_summary
from resources.lib.strings import getString, NOW_SCROBBLING, MARKED_AS_WATCHED

# Module version
__version__ = '7.3.0'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] scrobbler.py v{__version__} - Core scrobbler engine loading', level=xbmc.LOGINFO)


class SimklScrobbler:
    """
    Handles the actual scrobbling logic.
    
    Tracks what's playing, reports to SIMKL, and marks things
    as watched when the threshold is reached.
    """
    
    def __init__(self, api):
        """
        Initialize scrobbler.
        
        Args:
            api: SimklAPI instance for making API calls
        """
        self.api = api
        
        # Current playback state
        self.is_playing = False
        self.is_paused = False
        self.current_video = None
        self.current_video_info = None  # SIMKL-formatted info
        
        # Progress tracking
        self.watched_time = 0  # seconds
        self.video_duration = 1  # seconds (avoid div by zero)
        self.last_transition_check = 0
        self.last_progress_update = 0  # timestamp of last SIMKL progress update
        self.paused_at = 0
        
        log("SimklScrobbler initialized")
    
    def playback_started(self, data):
        """
        Handle playback started event.
        
        This is called when the player starts playing video.
        We identify the content and start scrobbling.
        
        Args:
            data: Dict with video info from player
        """
        log(f"Playback started: {data}")
        
        if not data:
            return
        
        # Reset state
        self.current_video = data
        self.current_video_info = None
        self.watched_time = 0
        self.paused_at = 0
        
        # Check exclusions BEFORE wasting time on API calls
        # This is where we filter out Live TV, HTTP, plugins, and custom paths
        # Note: The player also checks this, but we double-check here as a safety net
        file_path = data.get("file", "")
        if check_exclusion(file_path):
            log_debug(f"Playback excluded (scrobbler check): {file_path}")
            return
        
        # Check type-specific settings
        media_type = data.get("type", "movie")
        if media_type == "movie" and not get_setting_bool("scrobble_movie"):
            log("Movie scrobbling is disabled")
            return
        elif media_type == "episode" and not get_setting_bool("scrobble_episode"):
            log("TV show scrobbling is disabled")
            return
        
        # Verify we're still playing (user might have stopped)
        if not xbmc.Player().isPlayingVideo():
            log("Player stopped before we could start scrobbling")
            return
        
        # Wait for possible silent seek (resume from position)
        xbmc.sleep(1000)
        
        try:
            # Get current playback position and duration
            player = xbmc.Player()
            self.watched_time = player.getTime()
            self.video_duration = player.getTotalTime()
            
            if self.video_duration == 0:
                # Fallback durations if not available
                if media_type == "movie":
                    self.video_duration = 90 * 60  # 90 minutes
                else:
                    self.video_duration = 45 * 60  # 45 minutes
                log_warning(f"Using fallback duration: {self.video_duration}s")
                
        except Exception as e:
            log_error(f"Error getting playback info: {e}")
            return
        
        # Identify the content on SIMKL
        self.current_video_info = self._identify_content(data)
        
        if not self.current_video_info:
            log_warning("Could not identify content on SIMKL")
            return
        
        # We're officially scrobbling!
        self.is_playing = True
        self.is_paused = False
        self.last_transition_check = time.time()
        self.last_progress_update = time.time()
        
        # Send scrobble start to SIMKL
        response = self._scrobble("start")
        
        if response:
            title = data.get("title", "Unknown")
            if media_type == "episode":
                show = data.get("show_title", title)
                season = data.get("season", 0)
                episode = data.get("episode", 0)
                title = f"{show} S{season:02d}E{episode:02d}"
            
            if get_setting_bool("show_notifications"):
                notify(getString(NOW_SCROBBLING), title)
            
            log(f"Started scrobbling: {title}")
        else:
            log_warning("Failed to start scrobble - continuing without")
    
    def playback_paused(self):
        """Handle playback paused event."""
        if not self.is_playing:
            return
        
        log("Scrobble paused")
        self.is_paused = True
        self.paused_at = time.time()
        
        # Tell SIMKL we're paused
        self._scrobble("pause")
    
    def playback_resumed(self):
        """Handle playback resumed event."""
        if not self.is_playing:
            return
        
        log("Scrobble resumed")
        
        if self.is_paused:
            pause_duration = time.time() - self.paused_at
            log(f"Was paused for {pause_duration:.1f} seconds")
        
        self.is_paused = False
        self.paused_at = 0
        
        # Resume scrobbling
        self._scrobble("start")
    
    def playback_seek(self):
        """Handle playback seek event."""
        if not self.is_playing:
            return
        
        log("Seek detected, doing transition check")
        self.transition_check(is_seek=True)
    
    def playback_ended(self):
        """
        Handle playback ended/stopped event.
        
        This is where we decide if content should be marked as watched,
        and optionally prompt the user to rate the content.
        
        SIMKL API behavior:
        - /scrobble/stop with progress >= 80% -> marks as watched automatically
        - /scrobble/stop with progress < 80% -> saves as paused session (NOT watched)
        
        If the user's configured threshold is lower than 80%, we use the
        /sync/history endpoint as a fallback to explicitly mark as watched.
        """
        if not self.is_playing:
            return
        
        log("Playback ended")
        
        # Calculate final progress
        watched_percent = self._calculate_watched_percent()
        log(f"Final progress: {watched_percent:.1f}%")
        
        # Send stop scrobble (always send this to end the session)
        response = self._scrobble("stop")
        
        # Determine if content should be marked as watched
        # User's threshold from settings (default 70%)
        threshold = get_setting_int("scrobble_threshold", 70)
        meets_user_threshold = watched_percent >= threshold
        
        # SIMKL marks watched at 80%+ via /scrobble/stop
        SIMKL_WATCHED_THRESHOLD = 80
        simkl_marked_watched = watched_percent >= SIMKL_WATCHED_THRESHOLD
        
        was_marked_watched = False
        
        if meets_user_threshold:
            if simkl_marked_watched:
                # SIMKL handled it via /scrobble/stop - we're good
                was_marked_watched = True
                log(f"SIMKL marked as watched via scrobble/stop ({watched_percent:.1f}% >= 80%)")
            else:
                # User threshold met but SIMKL didn't mark it (progress < 80%)
                # Use history API as fallback to explicitly mark watched
                log(f"Progress {watched_percent:.1f}% meets user threshold ({threshold}%) but below SIMKL's 80% - using history API fallback")
                history_result = self._mark_watched_via_history()
                if history_result:
                    was_marked_watched = True
                    log("Successfully marked as watched via history API")
                else:
                    log_warning("Failed to mark as watched via history API fallback")
        
        if was_marked_watched and get_setting_bool("show_notifications"):
            title = self._get_display_title()
            notify(getString(MARKED_AS_WATCHED), title)
        
        # Check if we should prompt for rating
        # Rating is triggered AFTER the scrobble completes
        # Copy data to locals BEFORE resetting state, because the rating dialog
        # is modal and new playback could start during it (autoplay)
        should_rate = was_marked_watched and self.current_video and self.current_video_info
        rating_media_type = None
        rating_media_info = None
        rating_watched_time = self.watched_time
        rating_total_time = self.video_duration
        
        if should_rate:
            rating_media_type = self.current_video.get("type", "movie")
            rating_media_info = self._build_rating_info()
        
        # Reset state BEFORE showing rating dialog so new playback isn't blocked
        self._reset_state()
        
        # Now show rating dialog with local copies (safe from state corruption)
        if should_rate and rating_media_info:
            self._check_rating(rating_media_type, rating_media_info, 
                             rating_watched_time, rating_total_time)
    
    def _check_rating(self, media_type, media_info, watched_time, total_time):
        """
        Check if we should prompt for a rating.
        
        Called after playback ends and content was marked as watched.
        Uses pre-copied local data to avoid race conditions with autoplay.
        
        Args:
            media_type: "movie" or "episode"
            media_info: Dict with title, ids, season/episode info
            watched_time: Seconds watched
            total_time: Total duration in seconds
        """
        try:
            # Import here to avoid circular imports
            from resources.lib.rating import rating_check
            
            # Run the rating check (will show dialog if conditions are met)
            rating_check(
                media_type=media_type,
                media_info=media_info,
                watched_time=watched_time,
                total_time=total_time,
                api=self.api
            )
        except Exception as e:
            log_error(f"Error checking rating: {e}")
    
    def _build_rating_info(self):
        """
        Build media info dict for the rating system.
        
        Returns:
            Dict with title, ids, season/episode info
        """
        if not self.current_video or not self.current_video_info:
            return None
        
        media_type = self.current_video.get("type", "movie")
        
        if media_type == "movie":
            return {
                "title": self.current_video.get("title", "Unknown"),
                "year": self.current_video.get("year"),
                "ids": self.current_video_info.get("ids", {})
            }
        
        elif media_type == "episode":
            show_info = self.current_video_info.get("show", {})
            episode_info = self.current_video_info.get("episode", {})
            
            return {
                "title": self.current_video.get("title"),
                "show_title": self.current_video.get("show_title") or show_info.get("title"),
                "season": episode_info.get("season"),
                "episode": episode_info.get("number", episode_info.get("episode")),
                "ids": self.current_video_info.get("ids", {}),
                "show_ids": show_info.get("ids", {})
            }
        
        return None
    
    def transition_check(self, is_seek=False):
        """
        Regular check during playback.
        
        Called every second from the service main loop.
        Updates progress and detects multi-episode transitions where
        Kodi may seamlessly switch to the next episode without firing
        onPlayBackEnded/onPlayBackStopped events.
        
        Args:
            is_seek: True if called due to a seek event
        """
        if not self.is_playing or self.is_paused:
            return
        
        try:
            player = xbmc.Player()
            if not player.isPlayingVideo():
                return
            
            # Check for multi-episode transition (file changed without stop/start)
            try:
                current_file = player.getPlayingFile()
                stored_file = self.current_video.get("file", "") if self.current_video else ""
                
                if stored_file and current_file and current_file != stored_file:
                    log(f"Multi-episode transition detected: {stored_file} -> {current_file}")
                    # Simulate end of previous episode then let onAVStarted handle the new one
                    self.playback_ended()
                    return
            except Exception:
                pass  # getPlayingFile() can fail during transitions
            
            # Update watched time
            self.watched_time = player.getTime()
            
            now = time.time()
            
            # Do progress logging every 60 seconds
            if now - self.last_transition_check >= 60:
                self.last_transition_check = now
                progress = self._calculate_watched_percent()
                log(f"Scrobble progress: {progress:.1f}%")
            
            # Send periodic progress update to SIMKL every 15 minutes
            # SIMKL recommends these to keep the session alive and track progress
            if now - self.last_progress_update >= 900:  # 900 seconds = 15 minutes
                self.last_progress_update = now
                log("Sending periodic progress update to SIMKL")
                self._scrobble("start")  # Re-sending "start" updates the progress
                    
        except Exception as e:
            # This happens normally when playback stops
            log_debug(f"Transition check exception (normal during stop): {e}")
    
    def _identify_content(self, video_data):
        """
        Identify content on SIMKL.
        
        Searches SIMKL's database to find matching content.
        
        Args:
            video_data: Dict with video info from player
            
        Returns:
            Dict with SIMKL-formatted content info or None
        """
        media_type = video_data.get("type", "movie")
        title = video_data.get("title")
        year = video_data.get("year")
        
        if not title:
            log_error("No title available for identification")
            return None
        
        try:
            if media_type == "movie":
                return self._identify_movie(video_data)
            elif media_type == "episode":
                return self._identify_episode(video_data)
            else:
                log_error(f"Unsupported media type: {media_type}")
                return None
                
        except Exception as e:
            log_error(f"Error identifying content: {e}")
            return None
    
    def _identify_movie(self, video_data):
        """
        Identify a movie on SIMKL.
        
        Args:
            video_data: Dict with movie info
            
        Returns:
            SIMKL movie object or None
        """
        title = video_data.get("title")
        year = video_data.get("year")
        imdb_id = video_data.get("imdb_id")
        tmdb_id = video_data.get("tmdb_id")
        file_path = video_data.get("file", "")
        
        # Extract just the filename for SIMKL's file-based matching
        filename = ""
        if file_path:
            # Get just the filename, not the full path
            filename = file_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        
        # Build IDs dict if we have any
        ids = {}
        if imdb_id:
            ids["imdb"] = imdb_id
        if tmdb_id:
            ids["tmdb"] = tmdb_id
        
        # If we have IDs, we can use them directly
        if ids:
            log(f"Using IDs for movie lookup: {ids}")
            return {
                "title": title,
                "year": year,
                "ids": ids
            }
        
        # Otherwise, search SIMKL
        log(f"Searching SIMKL for movie: {title} ({year})")
        results = self.api.search_movie(title, year)
        
        if not results:
            log_warning(f"No SIMKL results for movie: {title}")
            # Try without year
            results = self.api.search_movie(title)
            
        if results:
            movie = results[0]
            log(f"Found movie on SIMKL: {movie.get('title')}")
            return {
                "title": movie.get("title"),
                "year": movie.get("year"),
                "ids": movie.get("ids", {})
            }
        
        # Last resort - use title/year + filename for SIMKL's file-based matching
        # This helps with ambiguous titles like "Crash" where title+year isn't enough
        log_warning(f"Using title-only fallback for: {title} (file: {filename})")
        result = {"title": title, "year": year}
        if filename:
            result["file"] = filename
        return result
    
    def _identify_episode(self, video_data):
        """
        Identify a TV episode on SIMKL.
        
        Args:
            video_data: Dict with episode info
            
        Returns:
            SIMKL show+episode object or None
        """
        show_title = video_data.get("show_title") or video_data.get("title")
        season = video_data.get("season")
        episode = video_data.get("episode")
        year = video_data.get("year")
        imdb_id = video_data.get("imdb_id")
        tvdb_id = video_data.get("tvdb_id")
        tmdb_id = video_data.get("tmdb_id")
        
        if season is None or episode is None:
            log_error("Missing season/episode number")
            return None
        
        # Build show IDs if we have any
        ids = {}
        if imdb_id:
            ids["imdb"] = imdb_id
        if tvdb_id:
            ids["tvdb"] = tvdb_id
        if tmdb_id:
            ids["tmdb"] = tmdb_id
        
        # Build show info
        show_info = {
            "title": show_title,
        }
        if year:
            show_info["year"] = year
        if ids:
            show_info["ids"] = ids
            log(f"Using IDs for show lookup: {ids}")
        else:
            # Search for show on SIMKL
            log(f"Searching SIMKL for show: {show_title}")
            results = self.api.search_tv(show_title, year)
            
            if results:
                show = results[0]
                show_info = {
                    "title": show.get("title"),
                    "ids": show.get("ids", {})
                }
                if show.get("year"):
                    show_info["year"] = show.get("year")
                log(f"Found show on SIMKL: {show.get('title')}")
        
        # Build episode info
        # SIMKL API requires "number" (not "episode") for the episode number field
        episode_info = {
            "season": season,
            "number": episode
        }
        
        log(f"Identified episode: {show_info.get('title')} S{season:02d}E{episode:02d}")
        
        return {
            "show": show_info,
            "episode": episode_info
        }
    
    def _scrobble(self, action):
        """
        Send scrobble to SIMKL API.
        
        Args:
            action: "start", "pause", or "stop"
            
        Returns:
            API response or None
        """
        if not self.current_video_info:
            log_debug("No current video info, skipping scrobble")
            return None
        
        # Calculate progress
        progress = self._calculate_watched_percent()
        
        media_type = self.current_video.get("type", "movie")
        
        log(f"Sending {action} scrobble at {progress:.1f}%")
        
        try:
            if media_type == "movie":
                return self.api.scrobble(
                    action=action,
                    movie=self.current_video_info,
                    progress=progress
                )
            elif media_type == "episode":
                return self.api.scrobble(
                    action=action,
                    show=self.current_video_info.get("show"),
                    episode=self.current_video_info.get("episode"),
                    progress=progress
                )
            else:
                log_error(f"Unknown media type for scrobble: {media_type}")
                return None
                
        except Exception as e:
            log_error(f"Error sending scrobble: {e}")
            return None
    
    def _mark_watched_via_history(self):
        """
        Mark current item as watched using the /sync/history endpoint.
        
        This is a fallback for when /scrobble/stop doesn't mark the item
        as watched (progress < 80%) but the user's configured threshold
        has been met. This matches the Trakt addon behavior.
        
        Returns:
            API response dict or None on failure
        """
        if not self.current_video_info:
            return None
        
        media_type = self.current_video.get("type", "movie")
        
        try:
            if media_type == "movie":
                movie_obj = dict(self.current_video_info)
                return self.api.add_to_history(movies=[movie_obj])
            
            elif media_type == "episode":
                show_info = self.current_video_info.get("show", {})
                episode_info = self.current_video_info.get("episode", {})
                
                show_obj = {
                    "title": show_info.get("title"),
                    "ids": show_info.get("ids", {}),
                    "seasons": [{
                        "number": episode_info.get("season", 1),
                        "episodes": [{
                            "number": episode_info.get("number", 0)
                        }]
                    }]
                }
                if show_info.get("year"):
                    show_obj["year"] = show_info["year"]
                
                return self.api.add_to_history(shows=[show_obj])
            
            else:
                log_error(f"Unknown media type for history fallback: {media_type}")
                return None
                
        except Exception as e:
            log_error(f"Error marking watched via history: {e}")
            return None
    
    def _calculate_watched_percent(self):
        """
        Calculate percentage watched.
        
        Returns:
            Float percentage (0-100)
        """
        if self.video_duration <= 0:
            return 0
        
        # Floor the duration for consistent calculation
        floored_duration = math.floor(self.video_duration)
        if floored_duration <= 0:
            return 0
        
        return (self.watched_time / floored_duration) * 100
    
    def _get_display_title(self):
        """Get a nice display title for notifications."""
        if not self.current_video:
            return "Unknown"
        
        media_type = self.current_video.get("type", "movie")
        title = self.current_video.get("title", "Unknown")
        
        if media_type == "episode":
            show = self.current_video.get("show_title", title)
            season = self.current_video.get("season", 0)
            episode = self.current_video.get("episode", 0)
            return f"{show} S{season:02d}E{episode:02d}"
        
        return title
    
    def _reset_state(self):
        """Reset scrobbler state after playback ends."""
        self.is_playing = False
        self.is_paused = False
        self.current_video = None
        self.current_video_info = None
        self.watched_time = 0
        self.video_duration = 1
        self.last_progress_update = 0
        self.paused_at = 0
