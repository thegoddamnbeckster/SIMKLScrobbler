# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Script Entry Point
Version: 7.4.2
Last Modified: 2026-02-14

This file handles script calls from settings buttons and normal addon launches.
Professional code - Project 4 standards
"""

import sys
import json
import xbmc
import xbmcaddon
import xbmcgui
from resources.lib.auth import SimklAuth
from resources.lib.utils import log, log_error

# Version constant
VERSION = "7.4.2"

# Get addon instance
addon = xbmcaddon.Addon()

# Log version on module load
log(f"========================================")
log(f"default.py VERSION: {VERSION}")
log(f"========================================")


def update_auth_status():
    """
    Update the auth_status field in settings to reflect current state.
    This runs before opening settings so status is always current.
    
    CRITICAL: This is the ONLY place that should update auth_status!
    """
    log(f"[default.py v{VERSION}] ========== update_auth_status() START ==========")
    
    auth = SimklAuth()
    
    # Check if authenticated
    has_token = auth.is_authenticated()
    username = auth.get_username()
    
    log(f"[default.py v{VERSION}] has_token: {has_token}")
    log(f"[default.py v{VERSION}] username: '{username}'")
    
    if has_token and username:
        status = f"Authenticated as {username}"
        log(f"[default.py v{VERSION}] Setting status: '{status}'")
    elif has_token and not username:
        status = "Authenticated"
        log(f"[default.py v{VERSION}] Setting status: '{status}' (no username)")
    else:
        status = "Not currently signed in"
        log(f"[default.py v{VERSION}] Setting status: '{status}'")
    
    addon.setSetting('auth_status', status)
    log(f"[default.py v{VERSION}] Status updated successfully")
    log(f"[default.py v{VERSION}] ========== update_auth_status() END ==========")


def get_args_from_argv():
    """
    Parse all parameters from sys.argv.
    
    Settings buttons call: RunScript(script.simkl.scrobbler,action=auth)
    Context menus call: RunScript(script.simkl.scrobbler,action=rate,media_type=movie,dbid=123)
    
    Returns:
        dict: Parsed key=value pairs from argv
    """
    log(f"[default.py v{VERSION}] sys.argv: {sys.argv}")
    
    args = {}
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if '=' in arg:
                key, value = arg.split('=', 1)
                args[key] = value
                log(f"[default.py v{VERSION}] Parsed arg: {key}='{value}'")
    
    if not args:
        log(f"[default.py v{VERSION}] No args found")
    
    return args


def handle_auth_action():
    """
    Handle authentication button click.
    If already authenticated, ask for confirmation to re-authenticate.
    """
    log(f"[default.py v{VERSION}] ========== handle_auth_action() START ==========")
    
    auth = SimklAuth()
    
    # Check if already authenticated
    is_authenticated = auth.is_authenticated()
    username = auth.get_username() or "unknown user"
    
    log(f"[default.py v{VERSION}] is_authenticated: {is_authenticated}")
    log(f"[default.py v{VERSION}] username: '{username}'")
    
    if is_authenticated:
        # Already authenticated - confirm re-auth
        log(f"[default.py v{VERSION}] User is authenticated - showing re-auth confirmation")
        
        dialog = xbmcgui.Dialog()
        confirmed = dialog.yesno(
            "SIMKL Re-Authentication",
            f"You are currently logged in as {username}.[CR][CR]Do you want to re-authenticate with a different account?",
            nolabel="No",
            yeslabel="Yes"
        )
        
        if not confirmed:
            log(f"[default.py v{VERSION}] User declined re-auth - keeping current login")
            return
        
        log(f"[default.py v{VERSION}] User confirmed re-auth")
    else:
        log(f"[default.py v{VERSION}] User not authenticated - proceeding to auth")
    
    # Trigger authentication
    log(f"[default.py v{VERSION}] Calling auth.authenticate()...")
    success, username = auth.authenticate()
    log(f"[default.py v{VERSION}] auth.authenticate() returned: success={success}, username='{username}'")
    
    # Update status field directly using values from the dialog
    # (don't re-read settings - Kodi's async settings writes from background
    # threads may not be visible to a new Addon() instance yet)
    if success and username:
        status = f"Authenticated as {username}"
    elif success:
        status = "Authenticated"
    else:
        status = "Not currently signed in"
    
    log(f"[default.py v{VERSION}] Setting auth_status directly: '{status}'")
    addon.setSetting('auth_status', status)
    
    log(f"[default.py v{VERSION}] Authentication completed: success={success}")
    log(f"[default.py v{VERSION}] ========== handle_auth_action() END ==========")


def handle_signout_action():
    """
    Handle sign out button click.
    Clears token and username, confirms to user.
    """
    log(f"[default.py v{VERSION}] ========== handle_signout_action() START ==========")
    
    auth = SimklAuth()
    
    # Check if authenticated
    is_authenticated = auth.is_authenticated()
    log(f"[default.py v{VERSION}] is_authenticated: {is_authenticated}")
    
    if not is_authenticated:
        log(f"[default.py v{VERSION}] User not authenticated - showing message")
        xbmcgui.Dialog().ok(
            "SIMKL Sign Out",
            "You are not currently signed in."
        )
        return
    
    username = auth.get_username() or "unknown user"
    log(f"[default.py v{VERSION}] username: '{username}'")
    
    # Confirm sign out
    log(f"[default.py v{VERSION}] Showing sign out confirmation")
    dialog = xbmcgui.Dialog()
    confirmed = dialog.yesno(
        "SIMKL Sign Out",
        f"Are you sure you want to sign out?[CR][CR]Current user: {username}",
        nolabel="No",
        yeslabel="Yes"
    )
    
    if not confirmed:
        log(f"[default.py v{VERSION}] User cancelled sign out")
        return
    
    log(f"[default.py v{VERSION}] User confirmed sign out")
    
    # Clear authentication data
    log(f"[default.py v{VERSION}] Calling auth.clear_authentication()...")
    success = auth.clear_authentication()
    log(f"[default.py v{VERSION}] clear_authentication() returned: {success}")
    
    # Update status field after sign out
    log(f"[default.py v{VERSION}] Updating auth status after sign out...")
    update_auth_status()
    
    if success:
        log(f"[default.py v{VERSION}] User signed out: {username}")
        xbmcgui.Dialog().notification(
            "SIMKL",
            "Successfully signed out",
            xbmcgui.NOTIFICATION_INFO,
            3000
        )
    
    log(f"[default.py v{VERSION}] ========== handle_signout_action() END ==========")


def _get_kodi_item_info(media_type, dbid):
    """
    Get media info from Kodi library via JSON-RPC.
    
    Args:
        media_type: 'movie', 'episode', 'show', 'season'
        dbid: Kodi database ID
        
    Returns:
        dict with title, ids, season/episode info, or None
    """
    try:
        dbid = int(dbid)
    except (ValueError, TypeError):
        log_error(f"[default.py v{VERSION}] Invalid DBID: {dbid}")
        return None
    
    try:
        if media_type == 'movie':
            request = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "VideoLibrary.GetMovieDetails",
                "params": {
                    "movieid": dbid,
                    "properties": ["title", "year", "imdbnumber", "uniqueid"]
                }
            })
            response = json.loads(xbmc.executeJSONRPC(request))
            details = response.get("result", {}).get("moviedetails")
            if details:
                ids = {}
                uniqueid = details.get("uniqueid", {})
                if uniqueid.get("imdb"):
                    ids["imdb"] = uniqueid["imdb"]
                if uniqueid.get("tmdb"):
                    ids["tmdb"] = str(uniqueid["tmdb"])
                imdbnumber = details.get("imdbnumber", "")
                if imdbnumber.startswith("tt") and "imdb" not in ids:
                    ids["imdb"] = imdbnumber
                return {
                    "media_type": "movie",
                    "title": details.get("title", "Unknown"),
                    "year": details.get("year"),
                    "ids": ids,
                    "dbid": dbid
                }
        
        elif media_type == 'episode':
            request = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "VideoLibrary.GetEpisodeDetails",
                "params": {
                    "episodeid": dbid,
                    "properties": ["title", "showtitle", "season", "episode",
                                   "uniqueid", "tvshowid"]
                }
            })
            response = json.loads(xbmc.executeJSONRPC(request))
            details = response.get("result", {}).get("episodedetails")
            if details:
                # Get show-level IDs
                tvshowid = details.get("tvshowid")
                show_ids = {}
                if tvshowid:
                    show_req = json.dumps({
                        "jsonrpc": "2.0", "id": 1,
                        "method": "VideoLibrary.GetTVShowDetails",
                        "params": {
                            "tvshowid": tvshowid,
                            "properties": ["title", "year", "imdbnumber", "uniqueid"]
                        }
                    })
                    show_resp = json.loads(xbmc.executeJSONRPC(show_req))
                    show_details = show_resp.get("result", {}).get("tvshowdetails", {})
                    show_uniqueid = show_details.get("uniqueid", {})
                    if show_uniqueid.get("imdb"):
                        show_ids["imdb"] = show_uniqueid["imdb"]
                    if show_uniqueid.get("tvdb"):
                        show_ids["tvdb"] = str(show_uniqueid["tvdb"])
                    if show_uniqueid.get("tmdb"):
                        show_ids["tmdb"] = str(show_uniqueid["tmdb"])
                    imdbnumber = show_details.get("imdbnumber", "")
                    if imdbnumber.startswith("tt") and "imdb" not in show_ids:
                        show_ids["imdb"] = imdbnumber
                    elif imdbnumber.isdigit() and "tvdb" not in show_ids:
                        show_ids["tvdb"] = imdbnumber
                
                return {
                    "media_type": "episode",
                    "title": details.get("title", "Unknown"),
                    "show_title": details.get("showtitle", "Unknown"),
                    "season": details.get("season", 0),
                    "episode": details.get("episode", 0),
                    "ids": show_ids,
                    "show_ids": show_ids,
                    "dbid": dbid,
                    "tvshowid": tvshowid
                }
        
        elif media_type == 'show':
            request = json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "method": "VideoLibrary.GetTVShowDetails",
                "params": {
                    "tvshowid": dbid,
                    "properties": ["title", "year", "imdbnumber", "uniqueid"]
                }
            })
            response = json.loads(xbmc.executeJSONRPC(request))
            details = response.get("result", {}).get("tvshowdetails")
            if details:
                ids = {}
                uniqueid = details.get("uniqueid", {})
                if uniqueid.get("imdb"):
                    ids["imdb"] = uniqueid["imdb"]
                if uniqueid.get("tvdb"):
                    ids["tvdb"] = str(uniqueid["tvdb"])
                if uniqueid.get("tmdb"):
                    ids["tmdb"] = str(uniqueid["tmdb"])
                return {
                    "media_type": "show",
                    "title": details.get("title", "Unknown"),
                    "year": details.get("year"),
                    "ids": ids,
                    "dbid": dbid
                }
        
        log(f"[default.py v{VERSION}] No details found for {media_type} DBID {dbid}")
        return None
        
    except Exception as e:
        log_error(f"[default.py v{VERSION}] Error getting item info: {e}")
        return None


def handle_rate_action(media_type, dbid):
    """
    Handle context menu rate action.
    Looks up the item in Kodi, then shows the rating dialog.
    """
    log(f"[default.py v{VERSION}] ========== handle_rate_action() START ==========")
    log(f"[default.py v{VERSION}] media_type={media_type}, dbid={dbid}")
    
    # Check authentication
    auth = SimklAuth()
    if not auth.is_authenticated():
        xbmcgui.Dialog().notification("SIMKL", "Please authenticate first",
                                       xbmcgui.NOTIFICATION_WARNING, 3000)
        return
    
    # Get item info from Kodi
    item_info = _get_kodi_item_info(media_type, dbid)
    if not item_info:
        xbmcgui.Dialog().notification("SIMKL", "Could not find item details",
                                       xbmcgui.NOTIFICATION_ERROR, 3000)
        return
    
    try:
        from resources.lib.api import SimklAPI
        from resources.lib.rating import RatingService
        
        api = SimklAPI()
        rating_service = RatingService(api)
        
        # Build media info for rating dialog
        rating_media_info = {
            'media_type': item_info.get('media_type', 'movie'),
            'title': item_info.get('title', 'Unknown'),
            'simkl_id': item_info.get('ids', {}).get('simkl'),
            'imdb_id': item_info.get('ids', {}).get('imdb'),
            'tmdb_id': item_info.get('ids', {}).get('tmdb'),
            'tvdb_id': item_info.get('ids', {}).get('tvdb'),
        }
        
        # For shows, we rate the show itself (not as a movie)
        if media_type == 'show':
            rating_media_info['media_type'] = 'show'
            rating_media_info['title'] = item_info.get('title', 'Unknown')
            rating_media_info['ids'] = item_info.get('ids', {})
        
        rating_service.prompt_for_rating(rating_media_info)
        
    except Exception as e:
        log_error(f"[default.py v{VERSION}] Error in rate action: {e}")
        xbmcgui.Dialog().notification("SIMKL", "Rating failed",
                                       xbmcgui.NOTIFICATION_ERROR, 3000)
    
    log(f"[default.py v{VERSION}] ========== handle_rate_action() END ==========")


def handle_togglewatched_action(media_type, dbid):
    """
    Handle context menu toggle watched action.
    Marks item as watched/unwatched on SIMKL.
    """
    log(f"[default.py v{VERSION}] ========== handle_togglewatched_action() START ==========")
    log(f"[default.py v{VERSION}] media_type={media_type}, dbid={dbid}")
    
    # Check authentication
    auth = SimklAuth()
    if not auth.is_authenticated():
        xbmcgui.Dialog().notification("SIMKL", "Please authenticate first",
                                       xbmcgui.NOTIFICATION_WARNING, 3000)
        return
    
    # Get item info
    item_info = _get_kodi_item_info(media_type, dbid)
    if not item_info:
        xbmcgui.Dialog().notification("SIMKL", "Could not find item details",
                                       xbmcgui.NOTIFICATION_ERROR, 3000)
        return
    
    try:
        from resources.lib.api import SimklAPI
        api = SimklAPI()
        
        title = item_info.get('title', 'Unknown')
        ids = item_info.get('ids', {})
        
        if not ids:
            xbmcgui.Dialog().notification("SIMKL", "No IDs found for item",
                                           xbmcgui.NOTIFICATION_ERROR, 3000)
            return
        
        if media_type == 'movie':
            movie_obj = {
                "title": title,
                "ids": ids
            }
            if item_info.get('year'):
                movie_obj["year"] = item_info['year']
            
            result = api.add_to_history(movies=[movie_obj])
            if result:
                xbmcgui.Dialog().notification("SIMKL", f"Marked as watched: {title}",
                                               xbmcgui.NOTIFICATION_INFO, 3000)
            else:
                xbmcgui.Dialog().notification("SIMKL", "Failed to update SIMKL",
                                               xbmcgui.NOTIFICATION_ERROR, 3000)
        
        elif media_type == 'episode':
            show_obj = {
                "title": item_info.get('show_title', 'Unknown'),
                "ids": item_info.get('show_ids', ids),
                "seasons": [{
                    "number": item_info.get('season', 0),
                    "episodes": [{
                        "number": item_info.get('episode', 0)
                    }]
                }]
            }
            
            result = api.add_to_history(shows=[show_obj])
            show_title = item_info.get('show_title', 'Unknown')
            ep_label = f"S{item_info.get('season', 0):02d}E{item_info.get('episode', 0):02d}"
            if result:
                xbmcgui.Dialog().notification("SIMKL",
                    f"Marked as watched: {show_title} {ep_label}",
                    xbmcgui.NOTIFICATION_INFO, 3000)
            else:
                xbmcgui.Dialog().notification("SIMKL", "Failed to update SIMKL",
                                               xbmcgui.NOTIFICATION_ERROR, 3000)
        
        else:
            xbmcgui.Dialog().notification("SIMKL",
                f"Toggle watched not supported for {media_type}",
                xbmcgui.NOTIFICATION_WARNING, 3000)
    
    except Exception as e:
        log_error(f"[default.py v{VERSION}] Error in togglewatched: {e}")
        xbmcgui.Dialog().notification("SIMKL", "Toggle watched failed",
                                       xbmcgui.NOTIFICATION_ERROR, 3000)
    
    log(f"[default.py v{VERSION}] ========== handle_togglewatched_action() END ==========")


def handle_sync_action(media_type, dbid):
    """
    Handle context menu sync action.
    Syncs a single item to SIMKL immediately.
    """
    log(f"[default.py v{VERSION}] ========== handle_sync_action() START ==========")
    log(f"[default.py v{VERSION}] media_type={media_type}, dbid={dbid}")
    
    # For single item sync, just delegate to togglewatched
    # (same effect - marks item as watched on SIMKL)
    handle_togglewatched_action(media_type, dbid)
    
    log(f"[default.py v{VERSION}] ========== handle_sync_action() END ==========")


def main():
    """
    Main entry point for script calls.
    Handles action parameters or opens settings by default.
    """
    log(f"[default.py v{VERSION}] ==========================================")
    log(f"[default.py v{VERSION}] MAIN() CALLED")
    log(f"[default.py v{VERSION}] ==========================================")
    
    args = get_args_from_argv()
    action = args.get('action')
    media_type = args.get('media_type')
    dbid = args.get('dbid')
    
    log(f"[default.py v{VERSION}] Action: '{action}', media_type: '{media_type}', dbid: '{dbid}'")
    
    if action == 'auth':
        log(f"[default.py v{VERSION}] Routing to handle_auth_action()")
        handle_auth_action()
    elif action == 'signout':
        log(f"[default.py v{VERSION}] Routing to handle_signout_action()")
        handle_signout_action()
    elif action == 'rate':
        log(f"[default.py v{VERSION}] Routing to handle_rate_action()")
        handle_rate_action(media_type, dbid)
    elif action == 'togglewatched':
        log(f"[default.py v{VERSION}] Routing to handle_togglewatched_action()")
        handle_togglewatched_action(media_type, dbid)
    elif action == 'sync':
        log(f"[default.py v{VERSION}] Routing to handle_sync_action()")
        handle_sync_action(media_type, dbid)
    else:
        log(f"[default.py v{VERSION}] No action - updating status and opening settings")
        update_auth_status()
        addon.openSettings()
    
    log(f"[default.py v{VERSION}] ==========================================")
    log(f"[default.py v{VERSION}] MAIN() COMPLETE")
    log(f"[default.py v{VERSION}] ==========================================")


if __name__ == "__main__":
    main()
