# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler Rating Service
Version: 7.4.8
Last Modified: 2026-02-20

PHASE 9: Advanced Features & Polish

Handles rating dialog display and API submission for movies and TV episodes

IMPORTANT LIMITATION (as of 2025-12-24):
- SIMKL API does not support rating individual episodes
- Can only rate movies and entire TV shows
- Episode rating functionality is present in code but DISABLED by default
- When SIMKL adds episode rating support, simply enable the setting
- For now, only movie ratings are active

This module provides:
- Rating dialog presentation after playback completion
- Current rating retrieval from SIMKL
- Rating submission to SIMKL API
- User-friendly rating descriptions (1-10 scale)

PHASE 8: Complete localization - all strings now use getLocalizedString()
Rating descriptions moved to strings.po for easy translation

Professional code - Project 4 standards
"""

import xbmc
import xbmcgui
import xbmcaddon

from resources.lib import utils
from resources.lib.strings import (
    get_rating_description,
    getString,
    SIMKL,
    RATE_TITLE,
    CURRENT_RATING,
    CLICK_STAR,
    SELECT_RATING_FIRST,
    RATED_AS,
    SUBMIT_RATING_FAILED,
    RATING_DESC_FORMAT
)

# Module version
__version__ = '7.4.8'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] rating.py v{__version__} - Rating service module loading', level=xbmc.LOGINFO)


def rating_check(media_type, media_info, watched_time, total_time, api):
    """
    Check if rating prompt should be shown and handle the flow.
    
    This is a wrapper function called by scrobbler.py after playback ends.
    
    Args:
        media_type (str): 'movie' or 'episode'
        media_info (dict): Media information with title and IDs
        watched_time (float): Seconds watched
        total_time (float): Total video duration in seconds
        api: SIMKL API client instance
    """
    try:
        # Create rating service
        rating_service = RatingService(api)
        
        # Check if prompts are enabled for this media type
        if not rating_service.should_prompt_for_rating(media_type):
            utils.log(f"[rating v7.4.4] rating_check() Rating prompts disabled for {media_type}", xbmc.LOGDEBUG)
            return
        
        # Check minimum view time threshold
        min_view_pct = utils.get_setting_int("rating_min_view", 75)
        if total_time > 0:
            viewed_pct = (watched_time / total_time) * 100
            if viewed_pct < min_view_pct:
                utils.log(f"[rating v7.4.4] rating_check() Viewed {viewed_pct:.1f}% < minimum {min_view_pct}% for rating prompt")
                return
        
        # Build media info dict for rating dialog
        ids = media_info.get('ids', {})
        rating_media_info = {
            'media_type': media_type,
            'title': media_info.get('title', 'Unknown'),
            'simkl_id': ids.get('simkl'),
            'imdb_id': ids.get('imdb'),
            'tmdb_id': ids.get('tmdb'),
            'tvdb_id': ids.get('tvdb')
        }
        
        # We need at least ONE ID to submit a rating to SIMKL
        has_any_id = any([rating_media_info.get('simkl_id'),
                         rating_media_info.get('imdb_id'),
                         rating_media_info.get('tmdb_id'),
                         rating_media_info.get('tvdb_id')])
        if not has_any_id:
            utils.log(f"[rating v7.4.4] rating_check() No IDs available for rating - cannot rate", xbmc.LOGWARNING)
            return
        
        utils.log(f"[rating v7.4.4] rating_check() Rating check passed for '{rating_media_info['title']}' - showing dialog")
        
        # Show rating dialog
        rating_service.prompt_for_rating(rating_media_info)
        
    except Exception as e:
        utils.log(f"[rating v7.4.4] rating_check() Error in rating_check: {e}", xbmc.LOGERROR)


class RatingDialog(xbmcgui.WindowXMLDialog):
    """
    Custom rating dialog for SIMKL integration
    
    Displays 1-10 star rating interface with:
    - Horizontal star layout
    - Dynamic rating descriptions
    - Cancel and Submit buttons
    - Current rating display (if exists)
    """
    
    def __init__(self, *args, **kwargs):
        """Initialize rating dialog with media info and current rating"""
        super(RatingDialog, self).__init__()
        self.media_title = kwargs.get('title', 'Unknown')
        self.media_type = kwargs.get('media_type', 'unknown')  # 'movie' or 'episode'
        self.current_rating = kwargs.get('current_rating', None)
        self.selected_rating = None
        self.submitted = False
        
    def onInit(self):
        """Called when dialog is initialized - set up UI"""
        try:
            # Hide all gold stars first - XML has no <visible> tags so they
            # start visible. Python controls ALL star visibility.
            for i in range(1, 11):
                self.getControl(300 + i).setVisible(False)
            
            # Set title label
            title_label = self.getControl(100)
            title_label.setLabel(getString(RATE_TITLE).format(self.media_title))
            
            # Set initial description and star state
            if self.current_rating:
                desc_label = self.getControl(101)
                rating_desc = get_rating_description(self.current_rating)
                desc_label.setLabel(getString(CURRENT_RATING).format(
                    self.current_rating,
                    rating_desc
                ))
                
                # Highlight current rating stars
                self._highlight_stars(self.current_rating)
            else:
                desc_label = self.getControl(101)
                desc_label.setLabel(getString(CLICK_STAR))
                
        except Exception as e:
            utils.log("[rating v7.4.8] RatingDialog.onInit() Error initializing rating dialog: {}".format(str(e)), xbmc.LOGERROR)
    
    def onClick(self, controlId):
        """Handle button clicks"""
        # Star buttons are IDs 1-10
        if 1 <= controlId <= 10:
            self.selected_rating = controlId
            self._update_description(controlId)
            self._highlight_stars(controlId)
            
        # Submit button
        elif controlId == 9010:
            if self.selected_rating:
                self.submitted = True
                self.close()
            else:
                # No rating selected yet
                xbmcgui.Dialog().notification(
                    getString(SIMKL),
                    getString(SELECT_RATING_FIRST),
                    xbmcgui.NOTIFICATION_WARNING,
                    3000
                )
                
        # Cancel button
        elif controlId == 9000:
            self.submitted = False
            self.close()
    
    def onFocus(self, controlId):
        """Handle focus changes - preview stars and update description as user hovers.
        
        Shows gold stars up to hovered position as a preview.
        Does NOT change selected_rating - that only happens on click.
        """
        if 1 <= controlId <= 10:
            self._update_description(controlId)
            self._highlight_stars(controlId)
    
    def _update_description(self, rating):
        """Update rating description label with full description and meaning"""
        try:
            desc_label = self.getControl(101)
            description = get_rating_description(rating)
            desc_label.setLabel(getString(RATING_DESC_FORMAT).format(rating, description))
        except Exception as e:
            utils.log("[rating v7.4.7] RatingDialog._update_description() Error: {}".format(str(e)), xbmc.LOGERROR)
    
    def _highlight_stars(self, rating):
        """Set star visuals - gold for 1..rating, grey for (rating+1)..10.
        
        Uses paired image controls:
        - IDs 301-310: gold star images (shown when active)
        - IDs 201-210: grey star images (always visible as background)
        Gold images are layered on top of grey. We toggle gold visibility.
        """
        try:
            for i in range(1, 11):
                gold_star = self.getControl(300 + i)
                gold_star.setVisible(i <= rating)
        except Exception as e:
            utils.log("[rating v7.4.7] RatingDialog._highlight_stars() Error: {}".format(str(e)), xbmc.LOGERROR)


class RatingService:
    """
    Service for managing SIMKL ratings
    
    Handles:
    - Checking if rating prompts are enabled
    - Retrieving current ratings from SIMKL
    - Displaying rating dialog
    - Submitting ratings to SIMKL API
    """
    
    def __init__(self, api_client):
        """
        Initialize rating service
        
        Args:
            api_client: SIMKL API client instance
        """
        self.api = api_client
        self.addon = xbmcaddon.Addon()
        
    def should_prompt_for_rating(self, media_type):
        """
        Check if we should prompt for rating based on settings
        
        Args:
            media_type (str): 'movie' or 'episode'
            
        Returns:
            bool: True if rating prompt is enabled for this media type
            
        Note:
            For episodes, SIMKL rates the whole show (not individual episodes).
            The 'rating_prompt_shows' setting controls whether to prompt after
            watching an episode.
        """
        if media_type == 'movie':
            return self.addon.getSettingBool('rating_prompt_movies')
        elif media_type == 'episode':
            # SIMKL rates shows, not individual episodes
            # This prompts to rate the show after watching an episode
            try:
                return self.addon.getSettingBool('rating_prompt_shows')
            except Exception:
                return False
        return False
    
    def get_current_rating(self, media_type, simkl_id=None, imdb_id=None, tmdb_id=None):
        """
        Retrieve current user rating from SIMKL.
        
        Searches by any available ID (SIMKL, IMDb, TMDb).
        
        Args:
            media_type (str): 'movie' or 'episode'
            simkl_id (int, optional): SIMKL ID of the item
            imdb_id (str, optional): IMDb ID of the item
            tmdb_id (str, optional): TMDb ID of the item
            
        Returns:
            int or None: Current rating (1-10) or None if not rated
        """
        if not simkl_id and not imdb_id and not tmdb_id:
            return None
        
        try:
            api_type = 'movies' if media_type == 'movie' else 'shows'
            ratings_list = self.api.get_ratings(api_type)
            
            if not ratings_list:
                return None
            
            for item in ratings_list:
                if media_type == 'movie':
                    item_data = item.get('movie', {})
                else:
                    item_data = item.get('show', {})
                
                item_ids = item_data.get('ids', {})
                
                # Also check top-level ids (SIMKL API format varies)
                if not item_ids:
                    item_ids = item.get('ids', {})
                
                # Match on any available ID
                if simkl_id and item_ids.get('simkl') == simkl_id:
                    return item.get('user_rating', item.get('rating'))
                if imdb_id and item_ids.get('imdb') == imdb_id:
                    return item.get('user_rating', item.get('rating'))
                if tmdb_id and str(item_ids.get('tmdb', '')) == str(tmdb_id):
                    return item.get('user_rating', item.get('rating'))
            
            utils.log(f"[rating v7.4.4] RatingService.get_current_rating() No matching rating found in {len(ratings_list)} entries")
            return None
            
        except Exception as e:
            utils.log(f"[rating v7.4.4] RatingService.get_current_rating() Error retrieving current rating: {e}", xbmc.LOGERROR)
            return None
    
    def prompt_for_rating(self, media_info):
        """
        Display rating dialog and handle user input.
        
        Works with any available ID (SIMKL, IMDb, TMDb, TVDB) - does not
        require a SIMKL ID specifically.
        
        Args:
            media_info (dict): Media information including:
                - title (str): Display title
                - media_type (str): 'movie' or 'episode'
                - simkl_id (int, optional): SIMKL ID
                - imdb_id (str, optional): IMDb ID
                - tmdb_id (int, optional): TMDb ID
                - tvdb_id (int, optional): TVDB ID
                
        Returns:
            bool: True if rating was submitted successfully
        """
        try:
            media_type = media_info.get('media_type')
            title = media_info.get('title', 'Unknown')
            
            # Look up existing rating using any available ID
            current_rating = self.get_current_rating(
                media_type,
                simkl_id=media_info.get('simkl_id'),
                imdb_id=media_info.get('imdb_id'),
                tmdb_id=media_info.get('tmdb_id')
            )
            utils.log(f"[rating v7.4.4] RatingService.prompt_for_rating() Current rating lookup: {current_rating}")
            
            # Check rerating setting - fresh Addon() read to pick up recent changes
            try:
                allow_rerating = xbmcaddon.Addon().getSettingBool("rating_allow_rerating")
            except Exception:
                allow_rerating = False
            if current_rating and not allow_rerating:
                utils.log(f"[rating v7.4.4] RatingService.prompt_for_rating() Already rated ({current_rating}/10) and rerating disabled - skipping")
                return False
            
            # Show rating dialog
            dialog = RatingDialog(
                'script-simkl-rate.xml',
                self.addon.getAddonInfo('path'),
                'default',
                '720p',
                title=title,
                media_type=media_type,
                current_rating=current_rating
            )
            dialog.doModal()
            
            # Check if user submitted a rating
            if dialog.submitted and dialog.selected_rating:
                # Submit rating to SIMKL
                success = self.submit_rating(media_info, dialog.selected_rating)
                
                if success:
                    # Get the localized rating description
                    desc = get_rating_description(dialog.selected_rating)
                    
                    xbmcgui.Dialog().notification(
                        getString(SIMKL),
                        getString(RATED_AS).format(
                            title,
                            dialog.selected_rating,
                            desc
                        ),
                        xbmcgui.NOTIFICATION_INFO,
                        3000
                    )
                    return True
                else:
                    xbmcgui.Dialog().notification(
                        getString(SIMKL),
                        getString(SUBMIT_RATING_FAILED),
                        xbmcgui.NOTIFICATION_ERROR,
                        3000
                    )
                    return False
            
            # User cancelled
            return False
            
        except Exception as e:
            utils.log("[rating v7.4.4] RatingService.prompt_for_rating() Error prompting for rating: {}".format(str(e)), xbmc.LOGERROR)
            return False
    
    def submit_rating(self, media_info, rating):
        """
        Submit rating to SIMKL API
        
        Args:
            media_info (dict): Media information with IDs
            rating (int): Rating value (1-10)
            
        Returns:
            bool: True if successful
        """
        try:
            media_type = media_info.get('media_type')
            
            # Build api_media_info dict for api.add_rating()
            api_media_info = {
                'ids': {},
                'title': media_info.get('title', 'Unknown')
            }
            
            # Add all available IDs
            if media_info.get('simkl_id'):
                api_media_info['ids']['simkl'] = media_info['simkl_id']
            if media_info.get('imdb_id'):
                api_media_info['ids']['imdb'] = media_info['imdb_id']
            if media_info.get('tmdb_id'):
                api_media_info['ids']['tmdb'] = media_info['tmdb_id']
            if media_info.get('tvdb_id'):
                api_media_info['ids']['tvdb'] = media_info['tvdb_id']
            
            # Submit to SIMKL using correct api.add_rating signature:
            # add_rating(media_type, media_info, rating)
            utils.log("[rating v7.4.4] RatingService.submit_rating() Submitting rating {} for '{}' to SIMKL".format(
                rating, media_info.get('title', 'Unknown')
            ))
            
            response = self.api.add_rating(media_type, api_media_info, rating)
            
            if response:
                utils.log("[rating v7.4.4] RatingService.submit_rating() Rating submitted successfully")
                return True
            else:
                utils.log("[rating v7.4.4] RatingService.submit_rating() Failed to submit rating", xbmc.LOGERROR)
                return False
                
        except Exception as e:
            utils.log("[rating v7.4.4] RatingService.submit_rating() Error submitting rating: {}".format(str(e)), xbmc.LOGERROR)
            return False
