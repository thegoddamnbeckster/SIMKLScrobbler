# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Localization Helper
Version: 7.3.0
Last Modified: 2026-02-13

Provides easy access to localized strings throughout the addon.
Instead of repeating xbmcaddon.Addon().getLocalizedString() everywhere,
modules can just import getString() from this file.

Professional code - Project 4 standards
"""

import xbmc
import xbmcaddon

# Module version
__version__ = '7.3.0'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] strings.py v{__version__} - Localization helper loading', level=xbmc.LOGINFO)

# Cache the addon instance
_addon = xbmcaddon.Addon('script.simkl')


def getString(string_id):
    """
    Get a localized string by ID.
    
    Args:
        string_id (int): String ID from strings.po
        
    Returns:
        str: Localized string
    """
    return _addon.getLocalizedString(string_id)


# ============================================================================
# String ID Constants - Makes code more readable and IDE-friendly
# ============================================================================

# Authentication (32100-32199)
AUTH_STATUS = 32107

# Notifications (32700-32799)
NOW_SCROBBLING = 32710
MARKED_AS_WATCHED = 32711
SCROBBLE_FAILED = 32712
READY_TO_SCROBBLE = 32720
NOT_AUTHENTICATED_CONFIGURE = 32721
STARTING_SYNC_LIBRARY = 32722
STARTING_SYNC_SCHEDULED = 32723
SYNC_COMPLETE_COUNTS = 32724
SYNC_COMPLETE_NO_CHANGES = 32725
SYNC_FAILED = 32726
ADDON_NAME = 32727
SIMKL = 32728

# Rating (32800-32899)
RATE_TITLE = 32825
CURRENT_RATING = 32826
CLICK_STAR = 32827
SELECT_RATING_FIRST = 32828
RATED_AS = 32829
SUBMIT_RATING_FAILED = 32840
RATING_DESC_FORMAT = 32841

# Rating descriptions (32830-32839)
RATING_TRAIN_WRECK = 32830
RATING_TERRIBLE = 32831
RATING_POOR = 32832
RATING_BELOW_AVERAGE = 32833
RATING_AVERAGE = 32834
RATING_DECENT = 32835
RATING_GOOD = 32836
RATING_GREAT = 32837
RATING_EXCELLENT = 32838
RATING_LEGENDARY = 32839


def get_rating_description(rating):
    """
    Get the localized rating description for a given rating value.
    
    Args:
        rating (int): Rating value (1-10)
        
    Returns:
        str: Localized rating description
    """
    rating_strings = {
        1: RATING_TRAIN_WRECK,
        2: RATING_TERRIBLE,
        3: RATING_POOR,
        4: RATING_BELOW_AVERAGE,
        5: RATING_AVERAGE,
        6: RATING_DECENT,
        7: RATING_GOOD,
        8: RATING_GREAT,
        9: RATING_EXCELLENT,
        10: RATING_LEGENDARY
    }
    
    string_id = rating_strings.get(rating)
    if string_id:
        return getString(string_id)
    return ""
