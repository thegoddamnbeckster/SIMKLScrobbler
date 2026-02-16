# -*- coding: utf-8 -*-
"""
SIMKL Service - The Main Event Loop
Version: 7.3.4
Last Modified: 2026-02-14

This is the background service that makes scrobbling actually work.
Monitors playback events and sends data to SIMKL in real-time.

Architecture based on the Trakt addon pattern:
- Player sends events via callback to dispatch queue
- Monitor handles settings changes and library updates
- Main loop processes queue and does transition checks
- Scrobbler handles the actual SIMKL API calls

PHASE 8: Complete localization framework implemented
All user-facing strings now use getLocalizedString()

Professional code - Project 4 standards
"""

import xbmc
import xbmcaddon
import xbmcgui
import time
import threading
from collections import deque

# Import our modules
from resources.lib.scrobbler import SimklScrobbler
from resources.lib.api import SimklAPI
from resources.lib.utils import log, log_error, log_debug, get_setting, get_setting_bool, get_setting_int
from resources.lib.exclusions import check_exclusion, get_exclusion_summary
from resources.lib.sync import SyncManager
from resources.lib.strings import (
    getString,
    ADDON_NAME,
    READY_TO_SCROBBLE,
    NOT_AUTHENTICATED_CONFIGURE,
    STARTING_SYNC_LIBRARY,
    STARTING_SYNC_SCHEDULED,
    SYNC_COMPLETE_COUNTS,
    SYNC_COMPLETE_NO_CHANGES,
    SYNC_FAILED
)

# Module version
__version__ = '7.4.3'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] service.py v{__version__} - Main service module loading', level=xbmc.LOGINFO)


class SimklService:
    """
    The main service class that orchestrates everything.
    
    This is the central coordinator of the addon - it manages
    all components and routes events between them.
    """
    
    def __init__(self):
        """Initialize the service and all components."""
        self.dispatch_queue = deque()
        self.scrobbler = None
        self.player = None
        self.monitor = None
        self._running = False
        self._sync_in_progress = False
        self._sync_thread = None
        self._last_sync_time = None
        self._load_last_sync_time()
        log(f"[service v{__version__}] SimklService initialized - ready to scrobble!")
    
    def _dispatch_to_queue(self, data):
        """
        Add an event to the dispatch queue.
        
        Args:
            data: Dict containing action and any associated data
        """
        log_debug(f"Queuing dispatch: {data}")
        self.dispatch_queue.append(data)
    
    def _process_dispatch(self, data):
        """
        Process a dispatched event from the queue.
        
        This is where the magic happens - events from the player
        get translated into scrobbler actions.
        
        Args:
            data: Dict with 'action' key and any associated data
        """
        try:
            action = data.get("action")
            log_debug(f"Processing dispatch: {action}")
            
            if action == "started":
                # Playback started - identify and start scrobbling
                del data["action"]
                self.scrobbler.playback_started(data)
                
            elif action == "stopped" or action == "ended":
                # Playback stopped/ended - finalize scrobble
                self.scrobbler.playback_ended()
                
            elif action == "paused":
                # Playback paused - tell SIMKL we're taking a break
                self.scrobbler.playback_paused()
                
            elif action == "resumed":
                # Playback resumed - back to watching!
                self.scrobbler.playback_resumed()
                
            elif action == "seek":
                # User seeked - update progress
                self.scrobbler.playback_seek()
                
            elif action == "settings_changed":
                # Settings changed - might need to reload something
                log(f"[service v{__version__}] Settings changed - checking for auth triggers")
                self._check_auth_triggers()
                
            else:
                log_debug(f"Unknown dispatch action: {action}")
                
        except Exception as e:
            log_error(f"Error processing dispatch: {e}")
    
    def _check_auth_triggers(self):
        """
        Check if auth state changed and refresh API token if needed.
        """
        import threading
        tid = threading.current_thread().name
        log(f"[service v{__version__}] _check_auth_triggers() | thread={tid}")
        if self.scrobbler and hasattr(self.scrobbler, 'api'):
            self.scrobbler.api.refresh_token()
            log(f"[service v{__version__}] Refreshed API token after settings change")
    
    def _load_last_sync_time(self):
        """Load the last sync timestamp from addon settings."""
        try:
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            last_sync_str = addon.getSetting('last_auto_sync_time')
            if last_sync_str:
                self._last_sync_time = float(last_sync_str)
                log(f"Loaded last sync time: {time.ctime(self._last_sync_time)}")
            else:
                self._last_sync_time = None
                log("No previous sync time found")
        except Exception as e:
            log_error(f"Error loading last sync time: {e}")
            self._last_sync_time = None
    
    def _save_last_sync_time(self):
        """Save the current sync timestamp to addon settings."""
        try:
            addon = xbmcaddon.Addon('script.simkl.scrobbler')
            self._last_sync_time = time.time()
            addon.setSetting('last_auto_sync_time', str(self._last_sync_time))
            log(f"Saved last sync time: {time.ctime(self._last_sync_time)}")
        except Exception as e:
            log_error(f"Error saving last sync time: {e}")
    
    def _check_auth_status_on_startup(self):
        """
        Check authentication status and show notification.
        
        This runs once at startup to let the user know
        if they're authenticated or not.
        """
        addon = xbmcaddon.Addon('script.simkl.scrobbler')
        
        try:
            access_token = addon.getSetting('access_token')
            username = addon.getSetting('username')
            auth_status = addon.getSetting('auth_status')
            
            if access_token:
                # We have a token - we're authenticated!
                display_name = username if username else "SIMKL user"
                
                # Fix inconsistent state if needed
                if "Not Authenticated" in auth_status:
                    addon.setSetting('auth_status', f"Authenticated as {display_name}")
                    log("Fixed auth_status mismatch")
                
                log(f"Authenticated as {display_name}")
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(READY_TO_SCROBBLE).format(display_name),
                    xbmcgui.NOTIFICATION_INFO,
                    3000
                )
            else:
                # Not authenticated
                if "Not Authenticated" not in auth_status:
                    addon.setSetting('auth_status', "Not Authenticated")
                
                log("Not authenticated")
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(NOT_AUTHENTICATED_CONFIGURE),
                    xbmcgui.NOTIFICATION_WARNING,
                    3000
                )
                
        except Exception as e:
            log_error(f"Error checking auth status: {e}")
    
    def _check_scheduled_sync(self):
        """
        Check if it's time for a scheduled sync and trigger if needed.
        
        Returns True if sync was triggered, False otherwise.
        """
        try:
            # Get interval setting (in hours, 0 = off)
            # Note: This is a <select> setting - getSettingInt returns the index,
            # not the value. Use get_setting() to get the actual option value text.
            interval_str = get_setting('auto_sync_interval')
            try:
                interval_hours = int(interval_str) if interval_str else 0
            except (ValueError, TypeError):
                interval_hours = 0
            
            if interval_hours == 0:
                return False  # Scheduled sync is disabled
            
            # Check if sync is already in progress
            if self._sync_in_progress:
                log_debug("Sync already in progress, skipping scheduled check")
                return False
            
            # Check if enough time has passed
            if self._last_sync_time is None:
                # Never synced before - trigger now
                log(f"First scheduled sync (interval: {interval_hours}h)")
                self._trigger_scheduled_sync()
                return True
            
            current_time = time.time()
            elapsed_hours = (current_time - self._last_sync_time) / 3600.0
            
            if elapsed_hours >= interval_hours:
                log(f"Scheduled sync triggered ({elapsed_hours:.1f}h >= {interval_hours}h)")
                self._trigger_scheduled_sync()
                return True
            else:
                remaining = interval_hours - elapsed_hours
                log_debug(f"Next scheduled sync in {remaining:.1f} hours")
                return False
                
        except Exception as e:
            log_error(f"Error checking scheduled sync: {e}")
            return False
    
    def _trigger_scheduled_sync(self):
        """Trigger a scheduled sync in background thread."""
        show_notifications = False
        try:
            # Check if notifications are enabled
            show_notifications = get_setting_bool('show_library_sync_notifications')
            
            # Show notification that sync is starting (if enabled)
            if show_notifications:
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(STARTING_SYNC_SCHEDULED),
                    xbmcgui.NOTIFICATION_INFO,
                    3000
                )
            
            # Run sync in background thread
            sync_thread = threading.Thread(target=self._run_sync_thread)
            sync_thread.daemon = True
            sync_thread.start()
            
            log("Scheduled sync thread started")
            
        except Exception as e:
            log_error(f"Error triggering scheduled sync: {e}")
            if show_notifications:
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(SYNC_FAILED).format(str(e)),
                    xbmcgui.NOTIFICATION_ERROR,
                    5000
                )
    
    def _trigger_library_sync(self):
        """
        Trigger a full bidirectional sync in background thread.
        
        This runs sync in a separate thread to avoid blocking
        Kodi's library operations.
        """
        show_notifications = False
        try:
            # Check if notifications are enabled
            show_notifications = get_setting_bool('show_library_sync_notifications')
            
            # Show notification that sync is starting (if enabled)
            if show_notifications:
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(STARTING_SYNC_LIBRARY),
                    xbmcgui.NOTIFICATION_INFO,
                    3000
                )
            
            # Run sync in background thread
            sync_thread = threading.Thread(target=self._run_sync_thread, name="SIMKL-Sync")
            sync_thread.daemon = True
            sync_thread.start()
            self._sync_thread = sync_thread
            
            log(f"[service v{__version__}] Library sync thread started (thread={sync_thread.name})")
            
        except Exception as e:
            log_error(f"Error triggering library sync: {e}")
            if show_notifications:
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(SYNC_FAILED).format(str(e)),
                    xbmcgui.NOTIFICATION_ERROR,
                    5000
                )
    
    def _run_sync_thread(self):
        """
        Background thread that performs the actual sync.
        
        This method runs in a separate thread to avoid blocking.
        """
        sync_manager = None
        try:
            # Mark sync as in progress
            self._sync_in_progress = True
            
            log(f"[service v{__version__}] Running full bidirectional sync in background...")
            
            # Check if notifications are enabled
            show_notifications = get_setting_bool('show_library_sync_notifications')
            
            # Create sync manager
            # silent=True because service.py handles all user-facing notifications
            # SyncManager's own notifications would duplicate the service ones
            sync_manager = SyncManager(show_progress=False, silent=True)
            
            # Run bidirectional sync and capture stats
            # Sync TO SIMKL (export Kodi watched items)
            sync_manager.sync_to_simkl(
                sync_movies=get_setting_bool('sync_movies_from_kodi'),
                sync_episodes=get_setting_bool('sync_episodes_from_kodi')
            )
            
            # Sync FROM SIMKL (import SIMKL watched items)
            sync_manager.sync_from_simkl(
                sync_movies=get_setting_bool('sync_movies_to_kodi'),
                sync_episodes=get_setting_bool('sync_episodes_to_kodi')
            )
            
            # Get final stats
            stats = sync_manager.stats
            total_exported = stats['movies_exported'] + stats['episodes_exported']
            total_imported = stats['movies_imported'] + stats['episodes_imported']
            
            # Build completion message with counts
            if total_exported > 0 or total_imported > 0:
                message = getString(SYNC_COMPLETE_COUNTS).format(total_exported, total_imported)
            else:
                message = getString(SYNC_COMPLETE_NO_CHANGES)
            
            # Show completion notification (if enabled)
            if show_notifications:
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    message,
                    xbmcgui.NOTIFICATION_INFO,
                    3000
                )
            
            log(f"Library sync completed - Exported: {total_exported}, Imported: {total_imported}, Errors: {stats['errors']}")
            
            # Save last sync time (for scheduled syncs)
            self._save_last_sync_time()
                
        except Exception as e:
            log_error(f"Error in sync thread: {e}")
            # Import traceback for detailed error logging
            import traceback
            log_error(f"Traceback: {traceback.format_exc()}")
            
            if show_notifications:
                xbmcgui.Dialog().notification(
                    getString(ADDON_NAME),
                    getString(SYNC_FAILED).format(str(e)),
                    xbmcgui.NOTIFICATION_ERROR,
                    5000
                )
        finally:
            # Always clean up - critical for preventing file locks on uninstall
            if sync_manager:
                sync_manager.close()
                log(f"[service v{__version__}] Sync manager closed")
            self._sync_in_progress = False
            self._sync_thread = None
            log(f"[service v{__version__}] Sync thread finished")
    
    def run(self):
        """
        Main service loop - the core of the background service.
        
        Runs continuously until Kodi requests shutdown:
        1. Processes events from dispatch queue
        2. Does transition checks during playback
        3. Waits a bit before checking again
        """
        log("SIMKL Service starting main loop...")
        self._running = True
        
        # Wait for Kodi to fully start up
        startup_delay = 5  # Could make this a setting
        log(f"Waiting {startup_delay} seconds for Kodi startup...")
        
        # Create monitor first for abort checking
        self.monitor = SimklMonitor(
            action=self._dispatch_to_queue,
            service=self
        )
        
        if self.monitor.waitForAbort(startup_delay):
            log("Abort requested during startup delay")
            return
        
        # Check auth status and show notification
        self._check_auth_status_on_startup()
        
        # Trigger startup sync if enabled and authenticated
        addon = xbmcaddon.Addon('script.simkl.scrobbler')
        if get_setting_bool('sync_on_startup') and addon.getSetting('access_token'):
            log("Sync on startup enabled - triggering initial sync")
            self._trigger_library_sync()
        
        # Initialize scrobbler with API
        api = SimklAPI()
        self.scrobbler = SimklScrobbler(api)
        
        # Initialize player with callback to our dispatch queue
        self.player = SimklPlayer(action=self._dispatch_to_queue)
        
        log("Service initialized - entering main loop")
        
        # Track loop iterations for scheduled sync checking
        loop_count = 0
        
        # Main service loop
        while not self.monitor.abortRequested():
            # Process any queued events
            while self.dispatch_queue and not self.monitor.abortRequested():
                data = self.dispatch_queue.popleft()
                log_debug(f"Processing queued dispatch: {data}")
                self._process_dispatch(data)
            
            # Do transition check if playing video
            # This updates progress and handles multi-episode transitions
            if xbmc.Player().isPlayingVideo():
                self.scrobbler.transition_check()
            
            # Check for scheduled sync every 60 iterations (roughly every minute)
            loop_count += 1
            if loop_count >= 60:
                self._check_scheduled_sync()
                loop_count = 0
            
            # Wait 1 second before next iteration
            # This gives us responsive event handling while not hammering CPU
            if self.monitor.waitForAbort(1):
                break
        
        # Cleanup
        log(f"[service v{__version__}] Service shutting down...")
        self._running = False
        
        # Wait for sync thread to finish (max 3 seconds) so it can clean up
        if self._sync_thread and self._sync_thread.is_alive():
            log(f"[service v{__version__}] Waiting for sync thread to finish...")
            self._sync_thread.join(timeout=3)
            if self._sync_thread.is_alive():
                log_warning(f"[service v{__version__}] Sync thread did not finish in time")
        
        # Close API session to free socket connections (prevents file locks on uninstall)
        if hasattr(self, 'scrobbler') and self.scrobbler and hasattr(self.scrobbler, 'api'):
            self.scrobbler.api.close()
            log(f"[service v{__version__}] Scrobbler API session closed")
        
        if self.player:
            del self.player
        if self.monitor:
            del self.monitor
        
        log(f"[service v{__version__}] Service stopped")


class SimklPlayer(xbmc.Player):
    """
    Player monitor that detects playback events.
    
    Player monitor class that watches playback events and
    reports back to the service via the dispatch callback.
    """
    
    def __init__(self, *args, **kwargs):
        """
        Initialize player monitor.
        
        Args:
            action: Callback function to dispatch events
        """
        super(SimklPlayer, self).__init__()
        self.action = kwargs.get("action")
        self._playing = False
        self._current_file = None
        log("SimklPlayer initialized")
    
    def onAVStarted(self):
        """
        Called when Kodi starts playing audio/video.
        
        This is THE moment we've been waiting for!
        Time to figure out what's playing and start scrobbling.
        """
        # Give Kodi a moment to settle
        xbmc.sleep(1000)
        
        # Only care about video
        if not self.isPlayingVideo():
            log_debug("Not playing video, ignoring")
            return
        
        try:
            # Get the file being played
            self._current_file = self.getPlayingFile()
            log(f"[service v{__version__}] Video playback started: {self._current_file}")
            
            # Check exclusions (PVR, HTTP streams, etc.)
            if self._should_exclude(self._current_file):
                log(f"File excluded from scrobbling: {self._current_file}")
                return
            
            # Get video info and dispatch to service
            video_data = self._get_video_data()
            if video_data:
                video_data["action"] = "started"
                self._playing = True
                self.action(video_data)
            else:
                log("Could not get video data, skipping scrobble")
                
        except Exception as e:
            log_error(f"Error in onAVStarted: {e}")
    
    def onPlayBackStopped(self):
        """Called when user manually stops playback."""
        if self._playing:
            log("Playback stopped by user")
            self._playing = False
            self._current_file = None
            self.action({"action": "stopped"})
    
    def onPlayBackEnded(self):
        """Called when playback ends naturally."""
        if self._playing:
            log("Playback ended naturally")
            self._playing = False
            self._current_file = None
            self.action({"action": "ended"})
    
    def onPlayBackPaused(self):
        """Called when user pauses playback."""
        if self._playing:
            log("Playback paused")
            self.action({"action": "paused"})
    
    def onPlayBackResumed(self):
        """Called when playback resumes from pause."""
        if self._playing:
            log("Playback resumed")
            self.action({"action": "resumed"})
    
    def onPlayBackSeek(self, *args):
        """
        Called when user seeks.
        
        Note: Parameter semantics changed in Kodi 20 (Nexus):
        - Kodi 19 and earlier: (time_ms, offset_ms) in milliseconds
        - Kodi 20+: (time_sec, offset_sec) in seconds
        Values are logged but not used for scrobble logic.
        """
        if self._playing:
            log(f"Playback seek detected")
            self.action({"action": "seek"})
    
    def _should_exclude(self, file_path):
        """
        Check if file should be excluded from scrobbling.
        
        Uses the exclusions module to check all configured exclusions:
        - Live TV (pvr://)
        - HTTP sources
        - Plugin sources
        - Script playback
        - Custom paths
        
        Args:
            file_path: Path to the file being played
            
        Returns:
            True if should be excluded, False otherwise
        """
        return check_exclusion(file_path)
    
    def _get_video_data(self):
        """
        Get video information for current playback.
        
        Uses both Kodi's VideoInfoTag and JSON-RPC for complete info.
        
        Returns:
            Dict with video data or None
        """
        try:
            # Get basic info from player
            info_tag = self.getVideoInfoTag()
            
            # Determine media type
            media_type = info_tag.getMediaType()
            if not media_type:
                # Try to guess from available info
                if info_tag.getSeason() > 0 or info_tag.getEpisode() > 0:
                    media_type = "episode"
                elif info_tag.getTVShowTitle():
                    media_type = "episode"
                else:
                    media_type = "movie"
            
            # Build video data dict
            video_data = {
                "type": media_type,
                "title": info_tag.getTitle() or info_tag.getOriginalTitle(),
                "year": info_tag.getYear(),
                "file": self._current_file,
            }
            
            # Get IDs - these are crucial for SIMKL matching
            # getIMDBNumber() can return TMDb numeric IDs when scraped by TMDb scraper
            # so we need to validate the format before trusting it as an IMDb ID
            imdb_id = None
            raw_imdb = info_tag.getIMDBNumber()
            
            # First try the more reliable getUniqueID("imdb")
            try:
                unique_imdb = info_tag.getUniqueID("imdb")
                if unique_imdb:
                    # Ensure tt prefix
                    if unique_imdb.startswith("tt"):
                        imdb_id = unique_imdb
                    elif unique_imdb.isdigit():
                        imdb_id = f"tt{unique_imdb}"
            except:
                pass
            
            # Fall back to getIMDBNumber() only if it looks like a real IMDb ID
            if not imdb_id and raw_imdb:
                if raw_imdb.startswith("tt"):
                    imdb_id = raw_imdb
                # Pure numeric from getIMDBNumber could be TMDb ID - don't use as IMDb
            
            if imdb_id:
                video_data["imdb_id"] = imdb_id
            
            # Try to get other IDs via uniqueid
            try:
                tvdb_id = info_tag.getUniqueID("tvdb")
                if tvdb_id:
                    video_data["tvdb_id"] = tvdb_id
            except:
                pass
            
            try:
                tmdb_id = info_tag.getUniqueID("tmdb")
                if tmdb_id:
                    video_data["tmdb_id"] = str(tmdb_id)
            except:
                pass
            
            # If getIMDBNumber() returned a pure number (likely TMDb ID) and 
            # we don't have a TMDb ID yet, use it as TMDb
            if not video_data.get("tmdb_id") and raw_imdb and raw_imdb.isdigit():
                video_data["tmdb_id"] = raw_imdb
                log_debug(f"Using getIMDBNumber() value '{raw_imdb}' as TMDb ID (pure numeric, no tt prefix)")
            
            # TV-specific info
            if media_type == "episode":
                video_data["show_title"] = info_tag.getTVShowTitle() or video_data["title"]
                video_data["season"] = info_tag.getSeason()
                video_data["episode"] = info_tag.getEpisode()
                video_data["episode_title"] = info_tag.getTitle()
            
            log(f"[service v{__version__}] Video data extracted: {video_data}")
            return video_data
            
        except Exception as e:
            log_error(f"Error getting video data: {e}")
            return None


class SimklMonitor(xbmc.Monitor):
    """
    Monitor for Kodi system events.
    
    Watches for settings changes, library updates, etc.
    """
    
    def __init__(self, *args, **kwargs):
        """
        Initialize monitor.
        
        Args:
            action: Callback function to dispatch events
            service: Reference to parent SimklService instance
        """
        super(SimklMonitor, self).__init__()
        self.action = kwargs.get("action")
        self.service = kwargs.get("service")
        log("SimklMonitor initialized")
    
    def onSettingsChanged(self):
        """Called when addon settings are changed."""
        log(f"[service v{__version__}] Settings changed detected")
        self.action({"action": "settings_changed"})
    
    def onScanFinished(self, database):
        """
        Called when library scan finishes.
        
        Args:
            database: "video" or "music"
        """
        if database == "video":
            log("Video library scan finished")
            # Trigger sync if setting is enabled
            if get_setting_bool('sync_on_update'):
                log("Triggering sync after library scan...")
                self.service._trigger_library_sync()
    
    def onCleanFinished(self, database):
        """
        Called when library clean finishes.
        
        Args:
            database: "video" or "music"
        """
        if database == "video":
            log("Video library clean finished")
            # Trigger sync if setting is enabled
            if get_setting_bool('sync_on_update'):
                log("Triggering sync after library clean...")
                self.service._trigger_library_sync()


def main():
    """
    Entry point for the service.
    
    Creates the service and runs it.
    Main entry point for the background service.
    """
    addon = xbmcaddon.Addon('script.simkl.scrobbler')
    
    log("=" * 50)
    log(f"SIMKL Scrobbler Service v{__version__} Starting")
    log(f"Addon ID: {addon.getAddonInfo('id')}")
    log(f"Addon Version: {addon.getAddonInfo('version')}")
    log(f"Addon Path: {addon.getAddonInfo('path')}")
    log("=" * 50)
    
    # Log exclusion settings summary
    log(get_exclusion_summary())
    
    service = SimklService()
    service.run()
    
    log("=" * 50)
    log("SIMKL Scrobbler Service Stopped")
    log("=" * 50)


if __name__ == "__main__":
    main()
