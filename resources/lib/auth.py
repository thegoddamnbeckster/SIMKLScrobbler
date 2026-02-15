# -*- coding: utf-8 -*-
"""
SIMKL OAuth Authentication
Version: 7.3.4
Last Modified: 2026-02-14

PHASE 9: Advanced Features & Polish

Handles OAuth PIN flow for SIMKL authentication

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import xbmc
from resources.lib.utils import log, log_module_init, set_setting, get_setting
from resources.lib.auth_dialog import show_auth_dialog

# Module version
__version__ = '7.3.4'

# Log module initialization
log_module_init('auth.py', __version__)


class SimklAuth:
    """Handles SIMKL OAuth authentication using WindowXMLDialog"""
    
    def __init__(self):
        """Initialize auth handler"""
        log(f"[auth.py v{__version__}] SimklAuth instance created")
    
    def authenticate(self):
        """
        Perform OAuth PIN authentication flow using dialog
        
        Returns:
            tuple: (success: bool, username: str or None)
        """
        log(f"[auth.py v{__version__}] ========== authenticate() START ==========")
        log(f"[auth.py v{__version__}] Calling show_auth_dialog()...")
        
        success, username = show_auth_dialog()
        
        log(f"[auth.py v{__version__}] show_auth_dialog() returned: success={success}, username='{username}'")
        log(f"[auth.py v{__version__}] ========== authenticate() END ==========")
        
        return success, username
    
    def is_authenticated(self):
        """
        Check if user is authenticated
        
        Returns:
            True if access token exists, False otherwise
        """
        token = get_setting("access_token")
        has_token = bool(token)
        
        log(f"[auth.py v{__version__}] is_authenticated(): {has_token}")
        
        return has_token
    
    def get_access_token(self):
        """
        Get stored access token
        
        Returns:
            Access token string or None
        """
        token = get_setting("access_token")
        log(f"[auth.py v{__version__}] get_access_token(): {'YES (len=' + str(len(token)) + ')' if token else 'NO'}")
        
        return token
    
    def get_username(self):
        """
        Get stored username
        
        Returns:
            Username string or None
        """
        username = get_setting("simkl_user")
        log(f"[auth.py v{__version__}] get_username(): '{username}'")
        
        return username
    
    def clear_authentication(self):
        """Clear stored authentication"""
        log(f"[auth.py v{__version__}] ========== clear_authentication() START ==========")
        
        try:
            log(f"[auth.py v{__version__}] Clearing access_token...")
            set_setting("access_token", "")
            
            # Also clear legacy simkl_token for clean state
            set_setting("simkl_token", "")
            
            log(f"[auth.py v{__version__}] Clearing simkl_user...")
            set_setting("simkl_user", "")
            
            log(f"[auth.py v{__version__}] Clearing username...")
            set_setting("username", "")
            
            log(f"[auth.py v{__version__}] Clearing simkl_usercode...")
            set_setting("simkl_usercode", "")
            
            log(f"[auth.py v{__version__}] Authentication cleared successfully")
            log(f"[auth.py v{__version__}] ========== clear_authentication() END ==========")
            
            return True
        except Exception as e:
            log(f"[auth.py v{__version__}] EXCEPTION in clear_authentication: {e}", xbmc.LOGERROR)
            log(f"[auth.py v{__version__}] ========== clear_authentication() FAILED ==========")
            
            return False
