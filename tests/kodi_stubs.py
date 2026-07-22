# -*- coding: utf-8 -*-
"""
Minimal Kodi module stubs for unit tests.

Import this module BEFORE any addon code to satisfy the top-level
`import xbmc` / `import xbmcaddon` / `import xbmcgui` calls that
live in every scrobbler module.

Usage (at the top of each test file, before addon imports):
    import tests.kodi_stubs  # noqa: F401
"""
import sys
import types
from unittest.mock import MagicMock


def _install():
    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG = 0
    xbmc.LOGINFO = 2
    xbmc.LOGWARNING = 3
    xbmc.LOGERROR = 4
    xbmc.log = MagicMock()
    xbmc.sleep = MagicMock()
    xbmc.Player = MagicMock
    xbmc.Monitor = MagicMock
    xbmc.executeJSONRPC = MagicMock(return_value='{"result": {}}')
    xbmc.getCondVisibility = MagicMock(return_value=False)

    xbmcaddon = types.ModuleType('xbmcaddon')
    _addon = MagicMock()
    _addon.getSetting = MagicMock(return_value='')
    _addon.getSettingBool = MagicMock(return_value=False)
    _addon.getSettingInt = MagicMock(return_value=0)
    xbmcaddon.Addon = MagicMock(return_value=_addon)

    xbmcgui = types.ModuleType('xbmcgui')
    xbmcgui.Dialog = MagicMock
    xbmcgui.DialogProgress = MagicMock
    xbmcgui.WindowXMLDialog = MagicMock
    xbmcgui.NOTIFICATION_INFO = 0
    xbmcgui.NOTIFICATION_WARNING = 1
    xbmcgui.NOTIFICATION_ERROR = 2
    xbmcgui.Window = MagicMock

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = MagicMock(return_value='')

    for name, mod in [
        ('xbmc', xbmc), ('xbmcaddon', xbmcaddon),
        ('xbmcgui', xbmcgui), ('xbmcvfs', xbmcvfs),
    ]:
        sys.modules.setdefault(name, mod)


_install()
