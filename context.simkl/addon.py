# -*- coding: utf-8 -*-
"""
SIMKL Context Menu
Version: 1.0.3
Last Modified: 2026-05-25

Context menu addon that adds SIMKL actions to the right-click menu in Kodi.
Delegates to script.simkl.scrobbler for all actual functionality.

Currently implemented:
  - Rate on SIMKL (action=rate)

Planned (backlog):
  - Toggle Watched on SIMKL (action=togglewatched)
  - Sync to SIMKL (action=sync)

SIMKL supports only movie and show ratings — not individual episodes or seasons.
This addon resolves episodes and seasons to their parent show before calling the
scrobbler, so the scrobbler always receives a type it can handle (movie or show).

Professional code - Project 4 standards
"""

import xbmc
import xbmcgui
import traceback
import json

# Version constant
VERSION = "1.0.3"


def log(message):
    """Log message with addon prefix and version."""
    xbmc.log("[context.simkl v%s] %s" % (VERSION, message), xbmc.LOGINFO)


def log_error(message):
    """Log error message."""
    xbmc.log("[context.simkl v%s] ERROR: %s" % (VERSION, message), xbmc.LOGERROR)


def get_media_type():
    """
    Get media type from ListItem.DBTYPE.

    Uses ListItem.DBTYPE (works in all views) rather than Container.Content()
    (only works in main library views).

    Returns:
        str: "movie", "show", "season", "episode", or None
    """
    dbtype = xbmc.getInfoLabel("ListItem.DBTYPE")
    log("ListItem.DBTYPE = '%s'" % dbtype)

    if dbtype == "tvshow":
        media_type = "show"
    elif dbtype == "season":
        media_type = "season"
    elif dbtype == "episode":
        media_type = "episode"
    elif dbtype == "movie":
        media_type = "movie"
    else:
        media_type = None

    log("Detected media_type = '%s'" % media_type)
    return media_type


def resolve_rateable_item(media_type, dbid):
    """
    Resolve a Kodi library item to a SIMKL-rateable (movie or show) entity.

    SIMKL supports only movie and show ratings — not individual episodes or
    seasons. This function translates:
      - episode → show  (via GetEpisodeDetails → tvshowid)
      - season  → show  (via GetSeasonDetails  → tvshowid)
      - movie   → movie (pass-through)
      - show    → show  (pass-through)

    Args:
        media_type (str): "movie", "show", "season", or "episode"
        dbid (str|int): Kodi database ID of the selected item

    Returns:
        (rateable_type, rateable_dbid) tuple where both are str/int,
        or (None, None) if resolution fails.
    """
    if media_type in ("movie", "show"):
        return media_type, dbid

    if media_type == "episode":
        try:
            request = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "VideoLibrary.GetEpisodeDetails",
                "params": {
                    "episodeid": int(dbid),
                    "properties": ["tvshowid", "showtitle"]
                }
            })
            response = json.loads(xbmc.executeJSONRPC(request))
            details = response.get("result", {}).get("episodedetails")
            if details:
                tvshowid = details.get("tvshowid")
                if tvshowid:
                    show_title = details.get("showtitle", "")
                    log("Episode DBID %s → show '%s' (tvshowid=%s)" % (dbid, show_title, tvshowid))
                    return "show", tvshowid
                else:
                    log_error("Episode DBID %s returned no tvshowid — Kodi DB may be inconsistent" % dbid)
            else:
                log_error("GetEpisodeDetails returned no result for DBID %s" % dbid)
        except Exception as e:
            log_error("Failed to resolve episode DBID %s to show: %s" % (dbid, str(e)))
        return None, None

    if media_type == "season":
        try:
            request = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "VideoLibrary.GetSeasonDetails",
                "params": {
                    "seasonid": int(dbid),
                    "properties": ["tvshowid", "showtitle"]
                }
            })
            response = json.loads(xbmc.executeJSONRPC(request))
            details = response.get("result", {}).get("seasondetails")
            if details:
                tvshowid = details.get("tvshowid")
                if tvshowid:
                    show_title = details.get("showtitle", "")
                    log("Season DBID %s → show '%s' (tvshowid=%s)" % (dbid, show_title, tvshowid))
                    return "show", tvshowid
                else:
                    log_error("Season DBID %s returned no tvshowid — Kodi DB may be inconsistent" % dbid)
            else:
                log_error("GetSeasonDetails returned no result for DBID %s" % dbid)
        except Exception as e:
            log_error("Failed to resolve season DBID %s to show: %s" % (dbid, str(e)))
        return None, None

    # Unknown type — should not be reached given the visibility condition in addon.xml
    log_error("Unhandled media_type '%s'" % media_type)
    return None, None


if __name__ == '__main__':
    log("========================================")
    log("CONTEXT MENU TRIGGERED")
    log("========================================")

    try:
        dbid = xbmc.getInfoLabel("ListItem.DBID")
        media_type = get_media_type()

        log("DBID = '%s'" % dbid)
        log("Media Type = '%s'" % media_type)

        if not dbid:
            log_error("No DBID found - cannot rate")
        elif not media_type:
            log_error("Unknown media type - cannot rate")
        else:
            # Resolve episodes/seasons to their parent show so the scrobbler
            # always receives media_type=movie or media_type=show with the
            # correct library DBID. SIMKL does not rate individual episodes
            # or seasons; the show rating is the canonical unit.
            rateable_type, rateable_dbid = resolve_rateable_item(media_type, dbid)

            if rateable_type is None:
                log_error("Could not resolve %s DBID %s to a rateable item" % (media_type, dbid))
                xbmcgui.Dialog().notification(
                    "SIMKL Rating",
                    "Could not identify item for rating",
                    xbmcgui.NOTIFICATION_ERROR,
                    3000
                )
            else:
                command = "RunScript(script.simkl.scrobbler,action=rate,media_type=%s,dbid=%s)" % (
                    rateable_type, rateable_dbid
                )
                log("Executing: %s" % command)
                xbmc.executebuiltin(command)
                log("Command sent to script.simkl.scrobbler")

        log("========================================")
        log("CONTEXT MENU COMPLETE")
        log("========================================")

    except Exception as e:
        log_error("Exception in context menu: %s" % str(e))
        log_error("Traceback: %s" % traceback.format_exc())
