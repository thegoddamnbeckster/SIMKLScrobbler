# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Authentication Dialog Handler  
Version: 7.2.0
Last Modified: 2026-02-04

PHASE 9: Advanced Features & Polish

CRITICAL: Uses WindowXMLDialog with XML skin file!

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

# Module version
__version__ = '7.2.0'

# Log module initialization
xbmc.log(f'[SIMKL Scrobbler] auth_dialog.py v{__version__} - Auth dialog module loading', level=xbmc.LOGINFO)

# Optional QR code support
try:
    import pyqrcode
    HAS_QRCODE = True
    xbmc.log(f"[SIMKL Scrobbler] pyqrcode module available for QR generation", xbmc.LOGINFO)
except ImportError:
    HAS_QRCODE = False
    xbmc.log(f"[SIMKL Scrobbler] pyqrcode module not available - QR codes disabled", xbmc.LOGWARNING)

# Action codes
ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

# Control IDs (must match XML file!)
PIN_LABEL = 204
QR_IMAGE = 205
STATUS_LABEL = 206
CANCEL_BUTTON = 3001


class SIMKLAuthDialog(xbmcgui.WindowXMLDialog):
    """SIMKL Authentication Dialog using WindowXMLDialog"""
    
    def __init__(self, *args, **kwargs):
        super(SIMKLAuthDialog, self).__init__()
        xbmc.log(f"[SIMKL Scrobbler] Auth: Initializing WindowXMLDialog", xbmc.LOGINFO)
        
        self.addon = xbmcaddon.Addon('script.simkl')
        self.CLIENT_ID = 'ab02f10030b0d629ffada90e2bf6236c57f42256a9e94d243255392af7b391e7'
        
        # Auth state
        self.user_code = None
        self.device_code = None
        self.interval = 5
        self.expires_in = 300
        self.polling = False
        self.success = False
        self.closed = False
        
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
        """Request device code from SIMKL"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: start_auth_flow() START", xbmc.LOGINFO)
        
        status_control = self.getControl(STATUS_LABEL)
        status_control.setLabel("Contacting SIMKL...")
        
        try:
            url = "https://api.simkl.com/oauth/pin"
            params = {'client_id': self.CLIENT_ID, 'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob'}
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: POST {url}", xbmc.LOGDEBUG)
            
            response = requests.post(url, params=params, timeout=10)
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: Response status: {response.status_code}", xbmc.LOGDEBUG)
            response.raise_for_status()
            data = response.json()
            
            self.user_code = data.get('user_code')
            self.device_code = data.get('device_code')
            self.interval = data.get('interval', 5)
            self.expires_in = data.get('expires_in', 300)
            
            xbmc.log(f"[SIMKL Scrobbler] Auth: user_code={self.user_code}, interval={self.interval}s, expires={self.expires_in}s", xbmc.LOGINFO)
            
            if self.user_code and self.device_code:
                self.display_auth_info()
                self.start_polling()
            else:
                xbmc.log(f"[SIMKL Scrobbler] Auth: ERROR: Missing user_code or device_code", xbmc.LOGERROR)
                status_control.setLabel("Error: Invalid response")
                xbmc.sleep(3000)
                self.close()
                
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: EXCEPTION: {e}", xbmc.LOGERROR)
            import traceback
            xbmc.log(f"[SIMKL Scrobbler] Auth: {traceback.format_exc()}", xbmc.LOGERROR)
            status_control.setLabel(f"Error: {str(e)}")
            xbmc.sleep(3000)
            self.close()
            
    def display_auth_info(self):
        """Update UI with PIN and QR code"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: display_auth_info() START", xbmc.LOGDEBUG)
        
        pin_control = self.getControl(PIN_LABEL)
        pin_control.setLabel(self.user_code)
        xbmc.log(f"[SIMKL Scrobbler] Auth: PIN displayed: {self.user_code}", xbmc.LOGINFO)
        
        auth_url = f"https://simkl.com/pin/{self.user_code}"
        
        if not HAS_QRCODE:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Skipping QR (pyqrcode unavailable)", xbmc.LOGINFO)
            return
            
        try:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Generating QR code...", xbmc.LOGDEBUG)
            qr = pyqrcode.create(auth_url)
            qr_path = xbmcvfs.translatePath('special://temp/simkl_qr.png')
            qr.png(qr_path, scale=10)
            
            qr_control = self.getControl(QR_IMAGE)
            qr_control.setImage(qr_path)
            xbmc.log(f"[SIMKL Scrobbler] Auth: QR code displayed", xbmc.LOGINFO)
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: QR generation failed: {e}", xbmc.LOGWARNING)
            
    def start_polling(self):
        """Start polling for authentication in background thread"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: start_polling() START", xbmc.LOGINFO)
        
        self.polling = True
        status_control = self.getControl(STATUS_LABEL)
        status_control.setLabel("Checking for authorization...")
        
        def poll_thread():
            xbmc.log(f"[SIMKL Scrobbler] Auth: Polling thread started", xbmc.LOGINFO)
            attempts = 0
            max_attempts = 60  # 5 minutes
            
            while self.polling and not self.closed and attempts < max_attempts:
                attempts += 1
                
                time_remaining = (max_attempts - attempts) * self.interval
                status_control.setLabel(f"Waiting... ({time_remaining}s remaining)")
                
                if self.check_authorization():
                    xbmc.log(f"[SIMKL Scrobbler] Auth: *** AUTHORIZATION SUCCESS ***", xbmc.LOGINFO)
                    self.success = True
                    self.polling = False
                    
                    # Fetch username
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Fetching username...", xbmc.LOGINFO)
                    username = self.fetch_username()
                    
                    if username:
                        xbmc.log(f"[SIMKL Scrobbler] Auth: Username: {username}", xbmc.LOGINFO)
                        # Save to both setting IDs for compatibility
                        self.addon.setSetting('simkl_user', username)
                        self.addon.setSetting('username', username)
                    else:
                        xbmc.log(f"[SIMKL Scrobbler] Auth: WARNING: No username", xbmc.LOGWARNING)
                        self.addon.setSetting('simkl_user', "")
                        self.addon.setSetting('username', "")
                    
                    status_control.setLabel("Authentication successful!")
                    xbmc.sleep(2000)
                    self.close()
                    return
                    
                xbmc.sleep(self.interval * 1000)
                
            if not self.success and not self.closed:
                xbmc.log(f"[SIMKL Scrobbler] Auth: TIMEOUT after {attempts} attempts", xbmc.LOGWARNING)
                status_control.setLabel("Timed out (5 minutes)")
                xbmc.sleep(3000)
                self.close()
                
        thread = threading.Thread(target=poll_thread)
        thread.daemon = True
        thread.start()
        xbmc.log(f"[SIMKL Scrobbler] Auth: Polling thread launched", xbmc.LOGINFO)
        
    def check_authorization(self):
        """Check if user has authorized"""
        try:
            url = f"https://api.simkl.com/oauth/pin/{self.user_code}?client_id={self.CLIENT_ID}"
            
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                
                result_status = result.get('result')
                access_token = result.get('access_token')
                
                if result_status == 'OK' and access_token:
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Token received (len={len(access_token)})", xbmc.LOGINFO)
                    # Primary token setting - used by api.py, service.py, and auth.py
                    self.addon.setSetting('access_token', access_token)
                    # Legacy setting - kept for backward compatibility
                    self.addon.setSetting('simkl_token', access_token)
                    xbmc.log(f"[SIMKL Scrobbler] Auth: Token saved to access_token (primary) and simkl_token (legacy)", xbmc.LOGINFO)
                    return True
                    
            return False
            
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: Poll exception: {e}", xbmc.LOGERROR)
            return False
            
    def fetch_username(self):
        """Fetch username from SIMKL"""
        xbmc.log(f"[SIMKL Scrobbler] Auth: fetch_username() START", xbmc.LOGDEBUG)
        
        try:
            # Read from primary token setting (access_token), not legacy (simkl_token)
            access_token = self.addon.getSetting('access_token')
            
            if not access_token:
                xbmc.log(f"[SIMKL Scrobbler] Auth: ERROR: No token!", xbmc.LOGERROR)
                return None
                
            url = "https://api.simkl.com/users/settings"
            headers = {
                'Authorization': f'Bearer {access_token}',
                'simkl-api-key': self.CLIENT_ID
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            username = data.get('account', {}).get('name') or data.get('user', {}).get('name')
            
            if username:
                xbmc.log(f"[SIMKL Scrobbler] Auth: Username found: {username}", xbmc.LOGINFO)
                return username
            else:
                xbmc.log(f"[SIMKL Scrobbler] Auth: No username in response data", xbmc.LOGWARNING)
                return None
                
        except Exception as e:
            xbmc.log(f"[SIMKL Scrobbler] Auth: fetch_username exception: {e}", xbmc.LOGERROR)
            return None


def show_auth_dialog():
    """Show authentication dialog"""
    xbmc.log(f"[SIMKL Scrobbler] show_auth_dialog() called", xbmc.LOGINFO)
    
    addon = xbmcaddon.Addon('script.simkl')
    addon_path = xbmcvfs.translatePath(addon.getAddonInfo('path'))
    
    dialog = SIMKLAuthDialog('script-simkl-AuthDialog.xml', addon_path, 'Default', '720p')
    dialog.doModal()
    
    success = dialog.success
    xbmc.log(f"[SIMKL Scrobbler] Auth dialog closed: success={success}", xbmc.LOGINFO)
    
    del dialog
    return success
