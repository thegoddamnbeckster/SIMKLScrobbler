# -*- coding: utf-8 -*-
"""
Utility Functions for SIMKL Scrobbler
Version: 7.5.9
Last Modified: 2026-04-15

PHASE 9: Advanced Features & Polish

Provides logging, settings management, and helper functions.
Common utilities used throughout the addon.

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import os
import re
import unicodedata
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

# Module version
__version__ = '7.9.7'

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
    Log a verbose/debug message, gated by the 'debug_logging' addon setting.

    When debug_logging is OFF (default): message is suppressed entirely.
    When debug_logging is ON: message is emitted at LOGINFO level so it
    appears in the standard Kodi log without requiring Kodi's own debug
    mode to be active.

    Using LOGINFO instead of LOGDEBUG is intentional: LOGDEBUG is only
    surfaced by Kodi when Kodi's global debug mode is enabled, making an
    addon-level toggle that writes at LOGDEBUG effectively invisible to
    users who haven't also enabled Kodi's debug mode. LOGINFO is always
    written, which is the expected behaviour for "enable verbose addon
    logging for troubleshooting."

    Args:
        message (str): Debug message to log
    """
    if get_setting_bool("debug_logging"):
        log(message, level=xbmc.LOGINFO)


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


# ---------------------------------------------------------------------------
# Ratings-cache path helper
# ---------------------------------------------------------------------------
# Shared by sync.py (write) and rating.py (read/patch).  Both modules call
# the same function so they always use identical paths without each
# constructing their own Addon() instance.


def get_ratings_cache_path(media_type):
    """
    Return the filesystem path for the ratings cache JSON file.

    Delegates to get_addon_profile() which is already backed by the lazily-
    initialised _ADDON singleton, so no extra Addon() construction occurs
    after the first call.

    Args:
        media_type (str): 'movies' or 'shows'

    Returns:
        str: Absolute filesystem path to the cache file
    """
    return os.path.join(get_addon_profile(), f'rating_cache_{media_type}.json')


# ---------------------------------------------------------------------------
# auto_sync_interval resolution
# ---------------------------------------------------------------------------
# v7.9.4 switched the setting from type="select" (stored actual hours:
# 0/1/6/12/24) to type="labelenum" (stores the selected index: 0–4).
# Values in _SYNC_INTERVAL_VALID_HOURS are used as-is (old stored hours);
# other values are looked up as labelenum indices.  0 and 1 exist in both
# schemes and map to the same result, so they are unambiguous.

_SYNC_INTERVAL_VALID_HOURS = frozenset({0, 1, 6, 12, 24})
_SYNC_INTERVAL_BY_INDEX = {0: 0, 1: 1, 2: 6, 3: 12, 4: 24}


def resolve_sync_interval_hours(stored_value):
    """
    Convert the stored auto_sync_interval setting to hours.

    Handles both old-format (hours value) and new-format (labelenum index)
    storage so existing user settings are preserved across the v7.9.4
    migration without a separate migration step.

    Args:
        stored_value (str): Raw value from getSetting('auto_sync_interval'),
                            or None/empty for a fresh install.

    Returns:
        int: Sync interval in hours.  0 means disabled; 6 is the default.
    """
    try:
        val = int(stored_value) if stored_value else 2  # '' → default index 2 → 6 h
        if val in _SYNC_INTERVAL_VALID_HOURS:
            return val                                   # Old format: stored hours
        return _SYNC_INTERVAL_BY_INDEX.get(val, 6)      # New format: stored index
    except (ValueError, TypeError):
        return 6                                         # Corrupt value → 6 h fallback


# ---------------------------------------------------------------------------
# Title normalization for title+year matching
# ---------------------------------------------------------------------------
# Different metadata sources encode the same title with different code
# points for visually-identical punctuation: TheTVDB/Kodi scrapers commonly
# use the single Unicode ellipsis character U+2026 ("What If…?"),
# while SIMKL/TMDB commonly spell it out as three ASCII periods
# ("What If...?"). A plain .lower().strip() treats these as different
# strings, so the title+year fallback match silently fails for any show
# whose title contains an ellipsis (or a curly apostrophe/quote) — even
# though the titles are visually identical. That failure is dangerous here:
# sync.py's "unmark not on SIMKL" pass treats an unmatched show as "confirmed
# absent from SIMKL" and resets every locally-watched episode to unwatched.
#
# NFKC compatibility normalization is applied first to fold a much broader
# class of visually-identical-but-different-codepoint variants automatically
# (non-breaking space U+00A0 -> regular space, full-width punctuation used by
# some anime/JP metadata sources, ligatures, etc.) without needing an
# explicit map entry for each one. It deliberately does NOT cover the
# ellipsis character or curly quotes below (Unicode does not define a
# compatibility decomposition for either), so those still need the explicit
# map. NFKC only folds codepoints Unicode itself defines as compatibility-
# equivalent, so it cannot introduce a false match between two genuinely
# different titles.
_TITLE_NORMALIZE_MAP = {
    "…": "...",  # HORIZONTAL ELLIPSIS -> three ASCII periods
    "‘": "'",    # LEFT SINGLE QUOTATION MARK
    "’": "'",    # RIGHT SINGLE QUOTATION MARK (curly apostrophe)
    "“": '"',    # LEFT DOUBLE QUOTATION MARK
    "”": '"',    # RIGHT DOUBLE QUOTATION MARK
}

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title(title):
    """
    Normalize a title for use as a title+year matching key.

    Applies Unicode NFKC compatibility normalization, folds punctuation
    variants (ellipsis character, curly quotes) to their plain-ASCII
    equivalents, lowercases, and collapses whitespace — so that titles from
    different metadata sources compare equal even when they use different
    code points or spacing for the same visible text.

    Args:
        title (str): Raw title string (may be None/empty).

    Returns:
        str: Normalized title, or "" if title was falsy.
    """
    if not title:
        return ""
    normalized = unicodedata.normalize("NFKC", title)
    for src, dst in _TITLE_NORMALIZE_MAP.items():
        normalized = normalized.replace(src, dst)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.lower().strip()


# ---------------------------------------------------------------------------
# IMDb ID rescue from mislabeled uniqueid entries
# ---------------------------------------------------------------------------
# Kodi's uniqueid dict is normally keyed by provider name ("imdb", "tvdb",
# "tmdb"), but some scraper configurations store an ID under a generic
# "unknown"/"unspecified" key instead of tagging its provider — code that
# only checks the named keys (uniqueid.get("imdb") etc.) silently treats
# that show as if it had no IDs at all, even though it was correctly
# identified by the scraper. An IMDb ID is unambiguous regardless of which
# key it's filed under: it always matches ^tt\d+$. This scans every value in
# a uniqueid dict for that pattern so a mislabeled IMDb ID is still found.
_IMDB_ID_RE = re.compile(r"^tt\d+$")


def find_imdb_id_in_uniqueid(uniqueid):
    """
    Find an IMDb ID anywhere in a Kodi uniqueid dict, regardless of its key.

    Args:
        uniqueid (dict): Kodi's uniqueid mapping (e.g. {"unknown": "tt123..."}).

    Returns:
        str or None: The IMDb ID ("tt1234567") if found, else None.
    """
    if not uniqueid:
        return None
    for value in uniqueid.values():
        if isinstance(value, str) and _IMDB_ID_RE.match(value):
            return value
    return None


# ---------------------------------------------------------------------------
# tvdb/tmdb collision guard
# ---------------------------------------------------------------------------
# Observed in the wild: a Kodi scraper attached the identical numeric value
# to both a show's tvdb_id and tmdb_id (e.g. tvdb=10771832, tmdb=10771832).
# TVDB and TMDB are independent numbering spaces maintained by unrelated
# organizations — a genuine coincidental match across both, for an arbitrary
# show, is for practical purposes impossible. This pattern means the
# scraper wrote one (usually wrong) value into both fields rather than
# actually resolving two distinct IDs.
#
# Trusting a bogus ID here is worse than having no ID at all: real-time
# scrobbling posts it straight to SIMKL's /scrobble endpoints, which may
# resolve it to an unrelated show or fail unpredictably, while the
# separate bulk sync path falls back to (correct) title+year matching.
# The result is the same episode intermittently appearing "on SIMKL" and
# "not on SIMKL" depending on which identification path ran — and because
# the bulk sync's "unmark not on SIMKL" safety net treats a real per-episode
# absence as authoritative, that oscillation can flip a genuinely-watched
# episode back to unwatched in Kodi.
#
# Dropping both IDs and forcing the title+year fallback (which is already
# proven reliable and consistent across every code path) is safer than
# guessing which of the two fields, if either, is correct.
def has_suspicious_tvdb_tmdb_collision(tvdb_id, tmdb_id):
    """
    Detect a Kodi scraper bug where tvdb_id and tmdb_id are identical.

    Args:
        tvdb_id: Raw tvdb id value (str/int/None) as delivered by Kodi.
        tmdb_id: Raw tmdb id value (str/int/None) as delivered by Kodi.

    Returns:
        bool: True if both are present and equal (as strings) — untrustworthy.
    """
    if not tvdb_id or not tmdb_id:
        return False
    return str(tvdb_id) == str(tmdb_id)


# ---------------------------------------------------------------------------
# Show identity for a playing episode
# ---------------------------------------------------------------------------
# The VideoInfoTag of a playing EPISODE carries the EPISODE's own ids (an episode
# has its own imdb/tvdb/tmdb id), not its show's. Sending those as the show's ids
# makes SIMKL fail to resolve the show by id and fall back to matching on the title
# alone -- and a title is not unique: SIMKL's anime catalogue has an entry literally
# titled "Supernatural" (2011), so a household watching the real Supernatural (2005)
# had its episodes recorded against that anime. The show's own ids live on the
# library's TV show entry; this reads them from there.
def get_show_identity_for_episode(episode_dbid):
    """
    Look up the TV show that owns a library episode.

    Args:
        episode_dbid (int): The episode's Kodi library id (> 0).

    Returns:
        dict or None: {"ids": {"imdb": str, "tvdb": str, "tmdb": str} (only those
        present), "year": int or None}; None when the episode is not in the library
        or Kodi could not say.
    """
    import json

    def rpc(method, params):
        try:
            raw = xbmc.executeJSONRPC(json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
            return (json.loads(raw) or {}).get("result") or {}
        except Exception:
            return {}

    try:
        episode_dbid = int(episode_dbid)
    except (TypeError, ValueError):
        return None
    if episode_dbid <= 0:
        return None

    episode = rpc("VideoLibrary.GetEpisodeDetails",
                  {"episodeid": episode_dbid, "properties": ["tvshowid"]}).get("episodedetails") or {}
    tvshowid = episode.get("tvshowid")
    if not tvshowid or tvshowid < 0:
        return None

    show = rpc("VideoLibrary.GetTVShowDetails",
               {"tvshowid": tvshowid, "properties": ["uniqueid", "imdbnumber", "year"]}).get("tvshowdetails") or {}
    if not show:
        return None

    uniqueid = show.get("uniqueid") or {}
    ids = {}
    imdb = uniqueid.get("imdb") or find_imdb_id_in_uniqueid(uniqueid)
    if imdb:
        ids["imdb"] = imdb
    for key in ("tvdb", "tmdb"):
        if uniqueid.get(key):
            ids[key] = str(uniqueid[key])
    year = show.get("year")
    return {"ids": ids, "year": year if isinstance(year, int) and year > 0 else None}


# End of utils.py
