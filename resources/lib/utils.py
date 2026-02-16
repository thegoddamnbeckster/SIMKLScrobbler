# -*- coding: utf-8 -*-
"""
Utility Functions for SIMKL Scrobbler
Version: 7.3.4
Last Modified: 2026-02-04

PHASE 9: Advanced Features & Polish

Provides logging, settings management, and helper functions.
Common utilities used throughout the addon.

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

# Module version
__version__ = '7.4.0'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] utils.py v{__version__} - Utility module loading', level=xbmc.LOGINFO)

# Addon instance - lazy loaded
_ADDON = None


def get_addon():
    """
    Get addon instance (lazy loaded).
    
    Returns:
        xbmcaddon.Addon instance
    """
    global _ADDON
    if _ADDON is None:
        _ADDON = xbmcaddon.Addon()
    return _ADDON


def log(message, level=xbmc.LOGINFO):
    """
    Log a message to the Kodi log with proper formatting.
    
    Args:
        message (str): Message to log
        level (int): Log level (LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR)
    """
    addon_name = get_addon().getAddonInfo('name')
    xbmc.log(f'[{addon_name}] {message}', level=level)


def log_error(message):
    """
    Log an error message.
    
    Args:
        message (str): Error message to log
    """
    log(message, level=xbmc.LOGERROR)


def log_debug(message):
    """
    Log a debug message.
    
    Only actually logs if debug_logging is enabled in settings.
    
    Args:
        message (str): Debug message to log
    """
    if get_setting_bool("debug_logging"):
        log(message, level=xbmc.LOGDEBUG)


def log_warning(message):
    """
    Log a warning message.
    
    Args:
        message (str): Warning message to log
    """
    log(message, level=xbmc.LOGWARNING)


def log_module_init(module_name, version):
    """
    Log module initialization with version.
    
    Standard logging function for module startup.
    
    Args:
        module_name (str): Name of the module
        version (str): Module version
    """
    xbmc.log(f'[SIMKL Scrobbler] {module_name} v{version} - Module loading', level=xbmc.LOGINFO)


def get_setting(setting_id):
    """
    Get an addon setting value as string.
    
    Args:
        setting_id (str): Setting identifier
        
    Returns:
        str: Setting value or empty string if not found
    """
    return get_addon().getSetting(setting_id)


def set_setting(setting_id, value):
    """
    Set an addon setting value.
    
    Args:
        setting_id (str): Setting identifier
        value (str): Value to set
    """
    get_addon().setSetting(setting_id, str(value))


def get_setting_int(setting_id, default=0):
    """
    Get an integer addon setting.
    
    Args:
        setting_id (str): Setting identifier
        default (int): Default value if not found
        
    Returns:
        int: Setting value
    """
    try:
        return get_addon().getSettingInt(setting_id)
    except:
        # Fallback: try to parse from string
        try:
            value = get_setting(setting_id)
            return int(value) if value else default
        except:
            return default


def get_setting_float(setting_id, default=0.0):
    """
    Get a float addon setting.
    
    Args:
        setting_id (str): Setting identifier
        default (float): Default value if not found
        
    Returns:
        float: Setting value
    """
    try:
        return get_addon().getSettingNumber(setting_id)
    except:
        # Fallback: try to parse from string
        try:
            value = get_setting(setting_id)
            return float(value) if value else default
        except:
            return default


def get_setting_bool(setting_id, default=False):
    """
    Get a boolean addon setting with default support.
    
    Args:
        setting_id (str): Setting identifier
        default (bool): Default value if not found
        
    Returns:
        bool: Setting value
    """
    try:
        return get_addon().getSettingBool(setting_id)
    except:
        return default


def notify(title, message, time_ms=None, icon_path=None):
    """
    Show a notification to the user.
    
    Args:
        title (str): Notification title
        message (str): Notification message
        time_ms (int): Display duration in milliseconds (uses setting if None)
        icon_path (str): Path to notification icon
    """
    # Check if notifications are enabled
    if not get_setting_bool("show_notifications", True):
        return
    
    # Get duration from settings if not specified
    if time_ms is None:
        time_ms = get_setting_int("notification_duration", 5000)
    
    # Use addon icon if not specified
    if icon_path is None:
        icon_path = get_addon().getAddonInfo('icon')
    
    # Show the notification
    xbmcgui.Dialog().notification(
        title,
        message,
        icon_path,
        time_ms
    )


def localize(string_id):
    """
    Get a localized string by ID.
    
    Args:
        string_id (int): String ID from strings.po
        
    Returns:
        str: Localized string or empty string if not found
    """
    return get_addon().getLocalizedString(string_id)


def get_addon_id():
    """
    Get the addon ID.
    
    Returns:
        str: Addon ID (e.g., "script.simkl.scrobbler")
    """
    return get_addon().getAddonInfo('id')


def get_addon_version():
    """
    Get the addon version.
    
    Returns:
        str: Addon version (e.g., "7.2.0")
    """
    return get_addon().getAddonInfo('version')


def get_addon_path():
    """
    Get the addon installation path.
    
    Returns:
        str: Full path to addon directory
    """
    return get_addon().getAddonInfo('path')


def get_addon_profile():
    """
    Get the addon profile (user data) path.
    
    Returns:
        str: Full path to addon profile directory
    """
    return xbmcvfs.translatePath(get_addon().getAddonInfo('profile'))


def format_time(seconds):
    """
    Format seconds into human-readable time.
    
    Args:
        seconds (float): Time in seconds
        
    Returns:
        str: Formatted time string (e.g., "1h 23m")
    """
    if seconds < 0:
        return "0s"
    
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


def format_progress(percent):
    """
    Format progress percentage.
    
    Args:
        percent (float): Progress percentage (0-100)
        
    Returns:
        str: Formatted percentage string
    """
    return f"{percent:.1f}%"


# End of utils.py
