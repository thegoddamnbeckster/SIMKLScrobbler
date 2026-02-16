# -*- coding: utf-8 -*-
"""
SIMKL Context Menu - Manual Sync Button
Version: 1.0.0
Last Modified: 2025-12-28

Context menu addon that adds "Sync to SIMKL now" option to media items.
Delegates to script.simkl.scrobbler for actual sync management.

Professional code - Project 4 standards
"""

import xbmc
import traceback

# Version constant
VERSION = "1.0.0"

def log(message):
    """Log message with addon prefix and version."""
    xbmc.log("[context.simkl.sync v%s] %s" % (VERSION, message), xbmc.LOGINFO)

def log_error(message):
    """Log error message."""
    xbmc.log("[context.simkl.sync v%s] ERROR: %s" % (VERSION, message), xbmc.LOGERROR)

def get_media_type():
    """
    Get media type from ListItem.DBTYPE instead of Container.Content.
    
    This works in all views (library, in progress, recently added, etc.)
    Container.Content() only works in main library views, but ListItem.DBTYPE
    works everywhere a media item exists.
    
    Returns:
        str: "movie", "episode", or None
    """
    dbtype = xbmc.getInfoLabel("ListItem.DBTYPE")
    log("ListItem.DBTYPE = '%s'" % dbtype)
    
    # Map Kodi's dbtype to our media_type
    if dbtype == "episode":
        media_type = "episode"
    elif dbtype == "movie":
        media_type = "movie"
    else:
        media_type = None
    
    log("Detected media_type = '%s'" % media_type)
    return media_type

if __name__ == '__main__':
    log("========================================")
    log("CONTEXT MENU TRIGGERED")
    log("========================================")
    
    try:
        # Get media details from ListItem
        dbid = xbmc.getInfoLabel("ListItem.DBID")
        media_type = get_media_type()
        
        log("DBID = '%s'" % dbid)
        log("Media Type = '%s'" % media_type)
        
        if not dbid:
            log_error("No DBID found - cannot sync")
        elif not media_type:
            log_error("Unknown media type - cannot sync")
        else:
            # Call script.simkl.scrobbler with sync action
            command = "RunScript(script.simkl.scrobbler,action=sync,media_type=%s,dbid=%s)" % (media_type, dbid)
            log("Executing: %s" % command)
            xbmc.executebuiltin(command)
            log("Command sent to script.simkl.scrobbler")
        
        log("========================================")
        log("CONTEXT MENU COMPLETE")
        log("========================================")
        
    except Exception as e:
        log_error("Exception in context menu: %s" % str(e))
        log_error("Traceback: %s" % traceback.format_exc())
