# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Authentication Dialog Handler  
Version: 7.3.4
Last Modified: 2026-02-14

BUG FIXES:
- FIXED: Dialog closing after ~3 seconds (NameError: max_attempts not in scope)
- FIXED: Separated API error handling from polling launch to prevent cascading close
- FIXED: Polling wait uses 500ms chunks for responsive cancel detection
- FIXED: Default expires_in now 900s (matches SIMKL API) not 300s
- IMPROVED: Time remaining display shows minutes:seconds format
- QR code generation via web API (removed broken pyqrcode dependency)
- Improved PIN polling with detailed response logging

Professional code - suitable for public distribution
Attribution: Claude.ai with assistance from Michael Beck
"""

import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
import requests
import threading
import time
import os

# Module version
__version__ = '7.4.1'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] auth_dialog.py v{__version__} - Auth dialog module loading', level=xbmc.LOGINFO)

# Action codes
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

# Control IDs (must match XML file!)
PIN_LABEL = 204
QR_IMAGE = 205
STATUS_LABEL = 206
CANCEL_BUTTON = 3001


def _download_qr_code(url, save_path):
    """
    Download a QR code image from a web API service.
    Uses qrserver.com free API - no key needed, no dependencies.
    
    Args:
        url: The URL to encode in the QR code
        save_path: Where to save the PNG file
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Use goqr.me API - free, no auth, returns PNG directly
        import urllib.parse
        encoded_url = urllib.parse.quote(url, safe='')
        qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={encoded_url}"
        
        xbmc.log(f"[SIMKL Scrobbler] Auth: Downloading QR code from API...", xbmc.LOGINFO)
        
        response = requests.get(qr_api_url, timeout=10)
        response.raise_for_status()
        
        # Verify we got image data
        content_type = response.headers.get('Content-Type', '')
        if 'image' not in content_type and len(response.content) < 100:
            xbmc.log(f"[SIMKL Scrobbler] Auth: QR API returned non-image: {content_type}", xbmc.LOGWARNING)
            return False
        
        # Save PNG file
        with open(save_path, 'wb') as f:
            f.write(response.content)
        
        xbmc.log(f"[SIMKL Scrobbler] Auth: QR code saved ({len(response.content)} bytes) to {save_path}", xbmc.LOGINFO)
        return True
        
    except Exception as e:
        xbmc.log(f"[SIMKL Scrobbler] Auth: QR download failed: {e}", xbmc.LOGWARNING)
        return False


class SIMKLAuthDialog(xbmcgui.WindowXMLDialog):
    """SIMKL Authentication Dialog using WindowXMLDialog"""
    
    def __init__(self, *args, **kwargs):
        super(SIMKLAuthDialog, self).__init__()
        xbmc.log(f"[SIMKL Scrobbler] Auth: Initializing WindowXMLDialog", xbmc.LOGINFO)
        
        self.addon = xbmcaddon.Addon('script.simkl.scrobbler')
        self.CLIENT_ID = 'ab02f10030b0d629ffada90e2bf6236c57f42256a9e94d243255392af7b391e7'
        
        # Auth state
        self.user_code = None
        self.device_code = None
        self.interval = 5
        self.expires_in = 900
        self.polling = False
        self.success = False
        self.closed = False
        self.fetched_username = None
        self._last_saved_token = None
        
        xbmc.log(f"[SIMKL Scrobbler] Auth: Dialog initialized", xbmc.LOGINFO)
        
    def onInit(self):
        """Called when dialog is first displayed"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: onInit() - dialog visible", xbmc.LOGINFO)
        self.start_auth_flow()
        
    def onAction(self, action):
        """Handle remote control / keyboard actions"""
        action_id = action.getId()
        xbmc.log(f"[SIMKL Scrobbler] Auth: onAction: action_id={action_id}", xbmc.LOGDEBUG)
        
        if action_id in [ACTION_PREVIOUS_MENU, ACTION_NAV_BACK]:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Back/Escape pressed", xbmc.LOGINFO)
            self.cancel_auth()
            
    def onClick(self, control_id):
        """Handle button clicks"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: onClick: control_id={control_id}", xbmc.LOGDEBUG)
        
        if control_id == CANCEL_BUTTON:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Cancel button clicked", xbmc.LOGINFO)
            self.cancel_auth()
            
    def cancel_auth(self):
        """Cancel authentication and close dialog"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: Cancelling authentication", xbmc.LOGINFO)
        self.closed = True
        self.polling = False
        self.success = False
        self.close()

    def start_auth_flow(self):
        """Request device code from SIMKL and start polling"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: start_auth_flow() START", xbmc.LOGINFO)
        
        status_control = self.getControl(STATUS_LABEL)
        status_control.setLabel("Contacting SIMKL...")
        
        try:
            url = "https://api.simkl.com/oauth/pin"
            params = {'client_id': self.CLIENT_ID, 'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob'}
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: GET {url} with params={params}", xbmc.LOGINFO)
            
            # SIMKL PIN endpoint uses GET, not POST
            response = requests.get(url, params=params, timeout=10)
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: Response status: {response.status_code}", xbmc.LOGINFO)
            xbmc.log(f"[SIMKL Scrobbler] Auth: Response body: {response.text[:500]}", xbmc.LOGINFO)
            response.raise_for_status()
            data = response.json()
            
            self.user_code = data.get('user_code')
            self.device_code = data.get('device_code')
            self.interval = data.get('interval', 5)
            self.expires_in = data.get('expires_in', 900)
            verification_url = data.get('verification_url', 'https://simkl.com/pin/')
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: user_code={self.user_code}, device_code={'YES' if self.device_code else 'NO'}", xbmc.LOGINFO)
            xbmc.log(f"[SIMKL Scrobbler] Auth: interval={self.interval}s, expires={self.expires_in}s, verify_url={verification_url}", xbmc.LOGINFO)
            
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: EXCEPTION in start_auth_flow: {e}", xbmc.LOGERROR)
            import traceback
            xbmc.log(f"[SIMKL Scrobbler] Auth: {traceback.format_exc()}", xbmc.LOGERROR)
            status_control.setLabel(f"Error: {str(e)}")
            xbmc.sleep(3000)
            self.close()
            return
        
        # Display auth info and start polling (outside the API try/except
        # so a bug here doesn't trigger the error-close handler)
        if self.user_code and self.device_code:
            self.display_auth_info()
            self.start_polling()
        else:
            xbmc.log(f"[SIMKL Scrobbler] Auth: ERROR: Missing user_code or device_code in response", xbmc.LOGERROR)
            status_control.setLabel("Error: Invalid response from SIMKL")
            xbmc.sleep(3000)
            self.close()

    def display_auth_info(self):
        """Update UI with PIN and QR code"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: display_auth_info() START", xbmc.LOGDEBUG)
        
        # Display the PIN code
        pin_control = self.getControl(PIN_LABEL)
        pin_control.setLabel(self.user_code)
        xbmc.log(f"[SIMKL Scrobbler] Auth: PIN displayed: {self.user_code}", xbmc.LOGINFO)
        
        # Generate QR code via web API
        auth_url = f"https://simkl.com/pin/{self.user_code}"
        qr_path = xbmcvfs.translatePath('special://temp/simkl_qr.png')
        
        # Remove old QR image if exists
        try:
            if os.path.exists(qr_path):
                os.remove(qr_path)
        except Exception:
            pass
        
        if _download_qr_code(auth_url, qr_path):
            try:
                qr_control = self.getControl(QR_IMAGE)
                qr_control.setImage(qr_path, useCache=False)
                xbmc.log(f"[SIMKL Scrobbler] Auth: QR code displayed successfully", xbmc.LOGINFO)
            except Exception as e:
                xbmc.log(f"[SIMKL Scrobbler] Auth: Failed to set QR image control: {e}", xbmc.LOGWARNING)
        else:
            xbmc.log(f"[SIMKL Scrobbler] Auth: QR code unavailable - user can still enter PIN manually", xbmc.LOGWARNING)

    def start_polling(self):
        """Start polling for authentication in background thread"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: start_polling() START", xbmc.LOGINFO)
        
        self.polling = True
        self.max_attempts = self.expires_in // self.interval
        status_control = self.getControl(STATUS_LABEL)
        status_control.setLabel("Waiting for authorization...")
        
        def poll_thread():
            xbmc.log(f"[SIMKL Scrobbler] Auth: Polling thread started", xbmc.LOGINFO)
            attempts = 0
            
            while self.polling and not self.closed and attempts < self.max_attempts:
                attempts += 1
                
                time_remaining = (self.max_attempts - attempts) * self.interval
                minutes = time_remaining // 60
                seconds = time_remaining % 60
                try:
                    status_control.setLabel(f"Waiting... ({minutes}:{seconds:02d} remaining)")
                except Exception:
                    pass  # Dialog might be closed
                
                auth_result = self.check_authorization()
                
                if auth_result == 'success':
                    xbmc.log(f"[SIMKL Scrobbler] Auth: *** AUTHORIZATION SUCCESS ***", xbmc.LOGINFO)
                    self.success = True
                    self.polling = False
                    
                    # Fetch username using the token we already have in memory
                    # (don't re-read from settings - Kodi's async writes may not be flushed yet)
                    access_token = self.addon.getSetting('access_token')
                    if not access_token:
                        # Fallback: read directly from what we just saved
                        xbmc.log(f"[SIMKL Scrobbler] Auth: getSetting returned empty - using token from memory", xbmc.LOGWARNING)
                        access_token = self._last_saved_token
                    
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Fetching username...", xbmc.LOGINFO)
                    username = self.fetch_username(access_token)
                    
                    if username:
                        xbmc.log(f"[SIMKL Scrobbler] Auth: Username: {username}", xbmc.LOGINFO)
                        self.addon.setSetting('simkl_user', username)
                        self.addon.setSetting('username', username)
                        self.fetched_username = username
                    else:
                        xbmc.log(f"[SIMKL Scrobbler] Auth: WARNING: No username retrieved", xbmc.LOGWARNING)
                        self.addon.setSetting('simkl_user', "")
                        self.addon.setSetting('username', "")
                    
                    try:
                        if username:
                            status_control.setLabel(f"Authenticated as {username}!")
                        else:
                            status_control.setLabel("Authentication successful!")
                    except Exception:
                        pass
                    xbmc.sleep(2000)
                    self.close()
                    return
                elif auth_result == 'error':
                    # Non-recoverable error
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Non-recoverable error during polling", xbmc.LOGERROR)
                    self.polling = False
                    try:
                        status_control.setLabel("Authentication error - please try again")
                    except Exception:
                        pass
                    xbmc.sleep(3000)
                    self.close()
                    return
                
                # 'pending' - wait before next poll
                # Use short sleeps so we can respond to cancel quickly
                for _ in range(self.interval * 2):
                    if not self.polling or self.closed:
                        return
                    xbmc.sleep(500)
                
            if not self.success and not self.closed:
                xbmc.log(f"[SIMKL Scrobbler] Auth: TIMEOUT after {attempts} attempts ({attempts * self.interval}s)", xbmc.LOGWARNING)
                try:
                    status_control.setLabel("Timed out - please try again")
                except Exception:
                    pass
                xbmc.sleep(3000)
                self.close()
                
        thread = threading.Thread(target=poll_thread)
        thread.daemon = True
        thread.start()
        xbmc.log(f"[SIMKL Scrobbler] Auth: Polling thread launched (max {self.max_attempts} attempts, {self.interval}s interval)", xbmc.LOGINFO)

    def check_authorization(self):
        """
        Check if user has authorized.
        
        Returns:
            'success' - Token received and saved
            'pending' - Still waiting for user
            'error' - Non-recoverable error
        """
        try:
            url = f"https://api.simkl.com/oauth/pin/{self.user_code}"
            params = {'client_id': self.CLIENT_ID}
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: Polling GET {url}", xbmc.LOGDEBUG)
            
            response = requests.get(url, params=params, timeout=10)
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: Poll response status={response.status_code}", xbmc.LOGDEBUG)
            xbmc.log(f"[SIMKL Scrobbler] Auth: Poll response body: {response.text[:500]}", xbmc.LOGDEBUG)
            
            if response.status_code == 200:
                result = response.json()
                
                result_status = result.get('result')
                access_token = result.get('access_token')
                
                xbmc.log(f"[SIMKL Scrobbler] Auth: Poll 200 - result='{result_status}', has_token={'YES' if access_token else 'NO'}", xbmc.LOGINFO)
                
                if result_status == 'OK' and access_token:
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Token received (len={len(access_token)})", xbmc.LOGINFO)
                    self._last_saved_token = access_token
                    # Save to both setting IDs for compatibility
                    self.addon.setSetting('access_token', access_token)
                    self.addon.setSetting('simkl_token', access_token)
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Token saved to access_token and simkl_token", xbmc.LOGINFO)
                    return 'success'
                elif access_token:
                    # Has token but result is not 'OK' - save anyway and log
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Token received with unexpected result='{result_status}' - saving anyway", xbmc.LOGWARNING)
                    self._last_saved_token = access_token
                    self.addon.setSetting('access_token', access_token)
                    self.addon.setSetting('simkl_token', access_token)
                    return 'success'
                else:
                    # 200 but no token yet - still pending
                    xbmc.log(f"[SIMKL Scrobbler] Auth: 200 but no token - result='{result_status}' - still pending", xbmc.LOGDEBUG)
                    return 'pending'
            
            elif response.status_code == 404:
                # Code not found or expired
                xbmc.log(f"[SIMKL Scrobbler] Auth: 404 - Code not found or expired", xbmc.LOGWARNING)
                return 'error'
            
            elif response.status_code == 400:
                # Bad request
                xbmc.log(f"[SIMKL Scrobbler] Auth: 400 - Bad request: {response.text[:200]}", xbmc.LOGWARNING)
                return 'pending'  # Might be transient
                
            else:
                # Other status - treat as pending
                xbmc.log(f"[SIMKL Scrobbler] Auth: Unexpected status {response.status_code}: {response.text[:200]}", xbmc.LOGWARNING)
                return 'pending'
                
        except requests.exceptions.Timeout:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Poll timeout - will retry", xbmc.LOGDEBUG)
            return 'pending'
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Poll exception: {e}", xbmc.LOGERROR)
            return 'pending'

    def fetch_username(self, access_token=None):
        """
        Fetch username from SIMKL after successful auth.
        
        Args:
            access_token: Token to use. If None, reads from settings (may fail
                          due to Kodi async settings flush timing).
        """
        xbmc.log(f"[SIMKL Scrobbler] Auth: fetch_username() START", xbmc.LOGDEBUG)
        
        try:
            if not access_token:
                access_token = self.addon.getSetting('access_token')
            
            if not access_token:
                xbmc.log(f"[SIMKL Scrobbler] Auth: ERROR: No token for username fetch!", xbmc.LOGERROR)
                return None
                
            url = "https://api.simkl.com/users/settings"
            headers = {
                'Authorization': f'Bearer {access_token}',
                'simkl-api-key': self.CLIENT_ID
            }
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: Fetching user settings from {url}", xbmc.LOGDEBUG)
            
            response = requests.get(url, headers=headers, timeout=10)
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: User settings status={response.status_code}", xbmc.LOGDEBUG)
            
            response.raise_for_status()
            data = response.json()
            
            # Try multiple paths for username
            username = (data.get('account', {}).get('name') or 
                       data.get('user', {}).get('name') or
                       data.get('account', {}).get('id'))
            
            if username:
                xbmc.log(f"[SIMKL Scrobbler] Auth: Username found: {username}", xbmc.LOGINFO)
                return str(username)
            else:
                xbmc.log(f"[SIMKL Scrobbler] Auth: No username in response. Keys: {list(data.keys())}", xbmc.LOGWARNING)
                return None
                
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: fetch_username exception: {e}", xbmc.LOGERROR)
            return None


def show_auth_dialog():
    """
    Show authentication dialog.
    
    Returns:
        tuple: (success: bool, username: str or None)
    """
    xbmc.log(f"[SIMKL Scrobbler] show_auth_dialog() called", xbmc.LOGINFO)
    
    addon = xbmcaddon.Addon('script.simkl.scrobbler')
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo('path'))
    
    dialog = SIMKLAuthDialog('script-simkl-AuthDialog.xml', addon_path, 'Default', '720p')
    dialog.doModal()
    
    success = dialog.success
    username = dialog.fetched_username
    xbmc.log(f"[SIMKL Scrobbler] Auth dialog closed: success={success}, username='{username}'", xbmc.LOGINFO)
    
    del dialog
    return success, username
