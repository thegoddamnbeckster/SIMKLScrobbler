# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Background Service Entry Point
Version: 7.2.0
Last Modified: 2026-02-05

This file is the entry point for Kodi's service extension.
It simply imports and runs the actual service from the lib folder.

Professional code - Project 4 standards
"""

import xbmc

# Log module initialization
xbmc.log('[SIMKL Scrobbler] service.py v7.2.0 - Entry point loading', level=xbmc.LOGINFO)

from resources.lib.service import main

if __name__ == "__main__":
    xbmc.log('[SIMKL Scrobbler] service.py v7.2.0 - Starting main service', level=xbmc.LOGINFO)
    main()
