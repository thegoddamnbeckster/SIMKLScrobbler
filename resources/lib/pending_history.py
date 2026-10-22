# -*- coding: utf-8 -*-
"""
SIMKL Scrobbler - Pending History Queue
Version: 7.9.24

Holds watches that could not be recorded on SIMKL when they happened, so a dropped connection, a SIMKL outage or a
rate limit at the moment a film ends does not lose the watch for good. (Until 7.9.24 a failed /sync/history call
for a finished watch was logged and forgotten; the next sync could not rebuild it for a rewatch, because Kodi's
play count of an already-watched item does not look "changed" to a sync that missed the moment.)

Each entry keeps the real finish time, so a retry hours later still records the watch when it happened, not when
the retry ran. Entries are retried by flush(), which the sync runs first thing.
"""

import json
from datetime import datetime, timezone

import xbmc
import xbmcvfs
from resources.lib.utils import log, log_error, log_warning
from resources.lib.api import summarize_history_result

__version__ = '7.9.26'

xbmc.log(f'[SIMKL Scrobbler] pending_history.py v{__version__} - Pending history queue loading', level=xbmc.LOGINFO)

_PENDING_FILE = xbmcvfs.translatePath(
    'special://userdata/addon_data/script.simkl.scrobbler/pending_history.json'
)

# SIMKL answered but could not match the item. Retrying cannot help, so give up after a few tries.
MAX_NOT_FOUND_STRIKES = 3
# Safety cap so a long outage cannot grow the file without bound; the oldest entries are dropped first.
MAX_ENTRIES = 200


class PendingHistory:
    """A small persistent list of history entries waiting to be sent to SIMKL."""

    def _load(self):
        try:
            if not xbmcvfs.exists(_PENDING_FILE):
                return []
            with xbmcvfs.File(_PENDING_FILE, 'r') as f:
                raw = f.read()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8')
            data = json.loads(raw) if raw else []
            return data if isinstance(data, list) else []
        except Exception as e:
            log_error(f"[pending_history v{__version__}] PendingHistory._load() {e}")
            return []

    def _save(self, entries):
        try:
            with xbmcvfs.File(_PENDING_FILE, 'w') as f:
                f.write(json.dumps(entries, indent=2))
        except Exception as e:
            log_error(f"[pending_history v{__version__}] PendingHistory._save() {e}")

    def add(self, kind, payload, label="", rewatch=False):
        """
        Queue one history entry.

        Args:
            kind: "movie" (payload is a movie object) or "show" (payload is a show object with seasons/episodes)
            payload: The object exactly as /sync/history takes it, including its watched_at
            label: Human-readable name for the log
            rewatch: True when this is a rewatch, so the retry is sent with allow_rewatch=yes
        """
        entries = self._load()
        entries.append({
            "kind": kind,
            "payload": payload,
            "label": label,
            "rewatch": bool(rewatch),
            "queued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "strikes": 0,
        })
        if len(entries) > MAX_ENTRIES:
            dropped = len(entries) - MAX_ENTRIES
            entries = entries[dropped:]
            log_warning(f"[pending_history v{__version__}] PendingHistory.add() Queue over {MAX_ENTRIES}; dropped the {dropped} oldest")
        self._save(entries)
        log(f"[pending_history v{__version__}] PendingHistory.add() Queued for retry: {label or kind} ({len(entries)} waiting)")

    def count(self):
        return len(self._load())

    def flush(self, api):
        """
        Try to send every queued entry. Returns (recorded, still_waiting).

        An entry leaves the queue when SIMKL recorded it, or already had it. A request that failed (no response)
        stays for the next flush. An entry SIMKL could not match is dropped after MAX_NOT_FOUND_STRIKES attempts.
        """
        entries = self._load()
        if not entries:
            return 0, 0

        log(f"[pending_history v{__version__}] PendingHistory.flush() Retrying {len(entries)} queued history entr{'y' if len(entries) == 1 else 'ies'}")
        remaining = []
        recorded = 0
        for entry in entries:
            kind = entry.get("kind")
            payload = entry.get("payload")
            label = entry.get("label") or kind
            rewatch = bool(entry.get("rewatch"))
            if kind == "movie":
                result = api.add_to_history(movies=[payload], allow_rewatch=rewatch)
            elif kind == "show":
                result = api.add_to_history(shows=[payload], allow_rewatch=rewatch)
            else:
                log_warning(f"[pending_history v{__version__}] PendingHistory.flush() Dropping entry of unknown kind {kind!r}")
                continue

            summary = summarize_history_result(result)
            if summary is None:
                remaining.append(entry)          # no response - try again next time
                continue
            _added, not_found = summary
            if not_found:
                entry["strikes"] = entry.get("strikes", 0) + 1
                if entry["strikes"] >= MAX_NOT_FOUND_STRIKES:
                    log_error(f"[pending_history v{__version__}] PendingHistory.flush() Giving up on {label}: SIMKL could not "
                              f"match it after {entry['strikes']} attempts")
                else:
                    remaining.append(entry)
                continue
            recorded += 1
            log(f"[pending_history v{__version__}] PendingHistory.flush() Recorded {label}")

        self._save(remaining)
        return recorded, len(remaining)
