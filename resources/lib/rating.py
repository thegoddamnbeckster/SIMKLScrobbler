# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler Rating Service
Version: 7.3.4
Last Modified: 2026-02-04

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
__version__ = '7.3.4'

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
            utils.log("Rating prompts disabled for {}".format(media_type), xbmc.LOGDEBUG)
            return
        
        # Build media info dict for rating dialog
        rating_media_info = {
            'media_type': media_type,
            'title': media_info.get('title', 'Unknown'),
            'simkl_id': media_info.get('ids', {}).get('simkl'),
            'imdb_id': media_info.get('ids', {}).get('imdb'),
            'tmdb_id': media_info.get('ids', {}).get('tmdb'),
            'tvdb_id': media_info.get('ids', {}).get('tvdb')
        }
        
        # Show rating dialog
        rating_service.prompt_for_rating(rating_media_info)
        
    except Exception as e:
        utils.log("Error in rating_check: {}".format(str(e)), xbmc.LOGERROR)


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
            # Set title label
            title_label = self.getControl(100)
            title_label.setLabel(getString(RATE_TITLE).format(self.media_title))
            
            # Set initial description
            if self.current_rating:
                desc_label = self.getControl(101)
                rating_desc = get_rating_description(self.current_rating)
                desc_label.setLabel(getString(CURRENT_RATING).format(
                    self.current_rating,
                    rating_desc
                ))
                
                # Highlight current rating star
                self._highlight_stars(self.current_rating)
            else:
                desc_label = self.getControl(101)
                desc_label.setLabel(getString(CLICK_STAR))
                
        except Exception as e:
            utils.log("Error initializing rating dialog: {}".format(str(e)), xbmc.LOGERROR)
    
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
        """Handle focus changes - update description as user hovers"""
        if 1 <= controlId <= 10:
            self._update_description(controlId)
    
    def _update_description(self, rating):
        """Update rating description label with full description and meaning"""
        try:
            desc_label = self.getControl(101)
            description = get_rating_description(rating)
            desc_label.setLabel(getString(RATING_DESC_FORMAT).format(rating, description))
        except Exception as e:
            utils.log("Error updating description: {}".format(str(e)), xbmc.LOGERROR)
    
    def _highlight_stars(self, rating):
        """Visual feedback - highlight stars up to selected rating"""
        # This would update star textures based on rating
        # For now, just update the focus
        try:
            self.setFocusId(rating)
        except Exception as e:
            utils.log("Error highlighting stars: {}".format(str(e)), xbmc.LOGERROR)


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
    
    def get_current_rating(self, media_type, simkl_id):
        """
        Retrieve current user rating from SIMKL
        
        Args:
            media_type (str): 'movie' or 'episode'
            simkl_id (int): SIMKL ID of the item
            
        Returns:
            int or None: Current rating (1-10) or None if not rated
        """
        try:
            # Determine API type parameter
            api_type = 'movies' if media_type == 'movie' else 'shows'
            
            # Get user's ratings for this type
            # api.get_ratings() returns a list of rated items directly
            ratings_list = self.api.get_ratings(api_type)
            
            if not ratings_list:
                return None
            
            # Search through the list for this specific item
            for item in ratings_list:
                # Extract item data based on type
                if media_type == 'movie':
                    item_data = item.get('movie', {})
                else:
                    item_data = item.get('show', {})
                
                item_ids = item_data.get('ids', {})
                if item_ids.get('simkl') == simkl_id:
                    return item.get('user_rating')
            
            return None
            
        except Exception as e:
            utils.log("Error retrieving current rating: {}".format(str(e)), xbmc.LOGERROR)
            return None
    
    def prompt_for_rating(self, media_info):
        """
        Display rating dialog and handle user input
        
        Args:
            media_info (dict): Media information including:
                - title (str): Display title
                - media_type (str): 'movie' or 'episode'
                - simkl_id (int): SIMKL ID
                - imdb_id (str, optional): IMDb ID
                - tmdb_id (int, optional): TMDb ID
                - tvdb_id (int, optional): TVDB ID
                
        Returns:
            bool: True if rating was submitted successfully
        """
        try:
            media_type = media_info.get('media_type')
            simkl_id = media_info.get('simkl_id')
            title = media_info.get('title', 'Unknown')
            
            if not simkl_id:
                utils.log("No SIMKL ID available for rating", xbmc.LOGWARNING)
                return False
            
            # Get current rating if exists
            current_rating = self.get_current_rating(media_type, simkl_id)
            
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
            utils.log("Error prompting for rating: {}".format(str(e)), xbmc.LOGERROR)
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
            utils.log("Submitting rating {} for '{}' to SIMKL".format(
                rating, media_info.get('title', 'Unknown')
            ))
            
            response = self.api.add_rating(media_type, api_media_info, rating)
            
            if response:
                utils.log("Rating submitted successfully")
                return True
            else:
                utils.log("Failed to submit rating", xbmc.LOGERROR)
                return False
                
        except Exception as e:
            utils.log("Error submitting rating: {}".format(str(e)), xbmc.LOGERROR)
            return False
