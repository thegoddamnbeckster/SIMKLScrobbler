# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Exclusions Module
Version: 7.3.4
Last Modified: 2026-02-04

PHASE 9: Advanced Features & Polish
- All exclusion settings now active in settings.xml
- Proper setting IDs matching this module

This module handles all the exclusion logic for the scrobbler.
Allows users to exclude certain content from being scrobbled.

Supports exclusions for:
- Live TV (pvr://) - For live television content
- HTTP/HTTPS sources - For streaming sources  
- Plugin sources - For addon-triggered playback
- Script sources - For script-triggered playback
- Specific paths - Up to 5 paths with subdirectories

Based on the Trakt addon's exclusion implementation.

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import xbmc
import xbmcgui
from resources.lib.utils import log, log_debug, log_module_init, get_setting, get_setting_bool

# Module version
__version__ = '7.4.3'

# Log module initialization
log_module_init('exclusions.py', __version__)


def check_exclusion(fullpath):
    """
    Check if the given file path should be excluded from scrobbling.
    
    This is called BEFORE we waste time identifying content on SIMKL.
    If this returns True, we skip scrobbling entirely.
    
    Args:
        fullpath: Full path/URL of the video file being played
        
    Returns:
        True if should be excluded, False if should be scrobbled
    """
    # No path provided - cannot scrobble
    if not fullpath:
        log_debug("check_exclusion: Empty path, excluding")
        return True
    
    # Live TV exclusion (pvr:// sources)
    if fullpath.startswith("pvr://") and get_setting_bool("ExcludeLiveTV"):
        log("Exclusion: Live TV source excluded (pvr://)")
        return True
    
    # HTTP/HTTPS exclusion
    if fullpath.startswith(("http://", "https://")) and get_setting_bool("ExcludeHTTP"):
        log("Exclusion: HTTP/HTTPS source excluded")
        return True
    
    # Plugin exclusion
    # For addon-triggered playback (plugin://)
    if fullpath.startswith("plugin://") and get_setting_bool("ExcludePlugin"):
        log("Exclusion: Plugin source excluded")
        return True
    
    # Script exclusion  
    # Check if another script is controlling playback
    # Uses window property like Trakt does
    if _is_script_paused() and get_setting_bool("ExcludeScript"):
        log("Exclusion: Script-controlled playback excluded")
        return True
    
    # Path exclusions (up to 5 paths)
    if _check_path_exclusions(fullpath):
        return True
    
    # No exclusions matched - proceed with scrobbling
    log_debug(f"check_exclusion: No exclusions matched for {fullpath}")
    return False


def _is_script_paused():
    """
    Check if another script has paused scrobbling.
    
    Some scripts set a window property to indicate they're
    controlling playback and don't want interference.
    
    Returns:
        True if script.simkl.scrobbler.paused property is set to "true"
    """
    window = xbmcgui.Window(10000)  # Home window
    return window.getProperty("script.simkl.scrobbler.paused") == "true"


def set_script_paused(paused):
    """
    Set or clear the script paused property.
    
    Other addons can call this to temporarily disable scrobbling.
    
    Args:
        paused: True to pause scrobbling, False to resume
    """
    window = xbmcgui.Window(10000)
    window.setProperty("script.simkl.scrobbler.paused", "true" if paused else "")
    log(f"Script paused property set to: {paused}")


def _check_path_exclusions(fullpath):
    """
    Check if the file path matches any of the configured excluded paths.
    
    Supports up to 5 path exclusions with cascading enable/disable.
    Paths are matched with startswith() so subdirectories are included.
    
    Args:
        fullpath: Full path of the video file
        
    Returns:
        True if path should be excluded, False otherwise
    """
    # Check first path exclusion (always available)
    if _check_single_path(fullpath, "ExcludePathOption", "ExcludePath", 1):
        return True
    
    # Check paths 2-5 (only if previous path is enabled - cascading)
    for i in range(2, 6):
        option_key = f"ExcludePathOption{i}"
        path_key = f"ExcludePath{i}"
        
        if _check_single_path(fullpath, option_key, path_key, i):
            return True
    
    return False


def _check_single_path(fullpath, option_key, path_key, path_number):
    """
    Check a single path exclusion.
    
    Args:
        fullpath: Full path of video file
        option_key: Settings key for enable toggle
        path_key: Settings key for path value
        path_number: Path number for logging
        
    Returns:
        True if this path exclusion matches
    """
    # Is this path exclusion enabled?
    if not get_setting_bool(option_key):
        return False
    
    # Get the excluded path
    excluded_path = get_setting(path_key)
    if not excluded_path:
        return False
    
    # Normalize paths for comparison (handle both forward and back slashes)
    # Windows uses backslashes, SMB uses forward slashes
    normalized_fullpath = fullpath.replace("\\", "/").lower()
    normalized_excluded = excluded_path.replace("\\", "/").lower()
    
    # Check if video path starts with excluded path
    if normalized_fullpath.startswith(normalized_excluded):
        log(f"Exclusion: Path {path_number} matched ({excluded_path})")
        return True
    
    return False


def get_exclusion_summary():
    """
    Get a summary of current exclusion settings for debugging.
    
    Returns:
        String summary of what's being excluded
    """
    exclusions = []
    
    if get_setting_bool("ExcludeLiveTV"):
        exclusions.append("Live TV (pvr://)")
    
    if get_setting_bool("ExcludeHTTP"):
        exclusions.append("HTTP/HTTPS sources")
    
    if get_setting_bool("ExcludePlugin"):
        exclusions.append("Plugin sources")
    
    if get_setting_bool("ExcludeScript"):
        exclusions.append("Script playback")
    
    # Count path exclusions
    path_count = 0
    if get_setting_bool("ExcludePathOption"):
        path_count += 1
    for i in range(2, 6):
        if get_setting_bool(f"ExcludePathOption{i}"):
            path_count += 1
    
    if path_count > 0:
        exclusions.append(f"{path_count} custom path(s)")
    
    if not exclusions:
        return "No exclusions configured"
    
    return "Excluding: " + ", ".join(exclusions)


# End of exclusions.py
