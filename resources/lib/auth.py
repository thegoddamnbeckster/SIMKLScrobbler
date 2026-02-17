# -*- coding: utf-8 -*-
"""
SIMKL OAuth Authentication
Version: 7.4.4
Last Modified: 2026-02-17

PHASE 9: Advanced Features & Polish

Handles OAuth PIN flow for SIMKL authentication

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import xbmc
from resources.lib.utils import log, log_module_init, set_setting, get_setting
from resources.lib.auth_dialog import show_auth_dialog

# Module version
__version__ = '7.4.4'

# Log module initialization
log_module_init('auth.py', __version__)


class SimklAuth:
    """Handles SIMKL OAuth authentication using WindowXMLDialog"""
    
    def __init__(self):
        """Initialize auth handler"""
        log(f"[auth v7.4.4] SimklAuth.__init__() SimklAuth instance created")
    
    def authenticate(self):
        """
        Perform OAuth PIN authentication flow using dialog
        
        Returns:
            tuple: (success: bool, username: str or None)
        """
        log(f"[auth v7.4.4] SimklAuth.authenticate() ========== authenticate() START ==========")
        log(f"[auth v7.4.4] SimklAuth.authenticate() Calling show_auth_dialog()...")
        
        success, username = show_auth_dialog()
        
        log(f"[auth v7.4.4] SimklAuth.authenticate() returned: success={success}, username='{username}'")
        log(f"[auth v7.4.4] SimklAuth.authenticate() ========== authenticate() END ==========")
        
        return success, username
    
    def is_authenticated(self):
        """
        Check if user is authenticated
        
        Returns:
            True if access token exists, False otherwise
        """
        token = get_setting("access_token")
        has_token = bool(token)
        
        log(f"[auth v7.4.4] SimklAuth.is_authenticated() : {has_token}")
        
        return has_token
    
    def get_access_token(self):
        """
        Get stored access token
        
        Returns:
            Access token string or None
        """
        token = get_setting("access_token")
        log(f"[auth v7.4.4] SimklAuth.get_access_token() : {'YES (len=' + str(len(token)) + ')' if token else 'NO'}")
        
        return token
    
    def get_username(self):
        """
        Get stored username
        
        Returns:
            Username string or None
        """
        username = get_setting("simkl_user")
        log(f"[auth v7.4.4] SimklAuth.get_username() : '{username}'")
        
        return username
    
    def clear_authentication(self):
        """Clear stored authentication"""
        log(f"[auth v7.4.4] SimklAuth.clear_authentication() ========== clear_authentication() START ==========")
        
        try:
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() Clearing access_token...")
            set_setting("access_token", "")
            
            # Also clear legacy simkl_token for clean state
            set_setting("simkl_token", "")
            
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() Clearing simkl_user...")
            set_setting("simkl_user", "")
            
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() Clearing username...")
            set_setting("username", "")
            
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() Clearing simkl_usercode...")
            set_setting("simkl_usercode", "")
            
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() Authentication cleared successfully")
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() ========== clear_authentication() END ==========")
            
            return True
        except Exception as e:
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() EXCEPTION in clear_authentication: {e}", xbmc.LOGERROR)
            log(f"[auth v7.4.4] SimklAuth.clear_authentication() ========== clear_authentication() FAILED ==========")
            
            return False
