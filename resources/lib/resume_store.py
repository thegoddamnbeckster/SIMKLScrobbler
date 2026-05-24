# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Resume Point Store
Version: 7.5.9

Persists playback positions for non-library streams (e.g. Umbrella/Real-Debrid)
that never touch Kodi's local library database. Keyed by stable media ID so
that temporary Real-Debrid URLs can change between sessions without losing the
resume position.
"""

import json
import xbmc
import xbmcvfs
from resources.lib.utils import log, log_error

__version__ = '7.8.1'

xbmc.log(f'[SIMKL Scrobbler] resume_store.py v{__version__} - Resume store loading', level=xbmc.LOGINFO)

_RESUME_FILE = xbmcvfs.translatePath(
    'special://userdata/addon_data/script.simkl.scrobbler/resume_points.json'
)


class ResumeStore:
    """
    Reads and writes resume positions keyed by stable media ID.

    Keys are constructed from SIMKL/IMDb/TMDb IDs so they remain valid
    across sessions even when stream URLs change.
    """

    def _load(self):
        try:
            if not xbmcvfs.exists(_RESUME_FILE):
                return {}
            with xbmcvfs.File(_RESUME_FILE, 'r') as f:
                raw = f.read()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            return json.loads(raw) if raw else {}
        except Exception as e:
            log_error(f"[resume_store v{__version__}] ResumeStore._load() {e}")
            return {}

    def _save(self, data):
        try:
            with xbmcvfs.File(_RESUME_FILE, 'w') as f:
                f.write(json.dumps(data, indent=2))
        except Exception as e:
            log_error(f"[resume_store v{__version__}] ResumeStore._save() {e}")

    def get(self, media_key):
        """Return saved position in seconds, or None if no resume point exists."""
        return self._load().get(media_key)

    def set(self, media_key, position_seconds):
        """Save resume position in seconds for the given media key."""
        data = self._load()
        data[media_key] = round(position_seconds)
        self._save(data)
        log(f"[resume_store v{__version__}] Saved: {media_key} = {round(position_seconds)}s")

    def delete(self, media_key):
        """Remove the resume position for the given media key."""
        data = self._load()
        if media_key in data:
            del data[media_key]
            self._save(data)
            log(f"[resume_store v{__version__}] Deleted: {media_key}")
