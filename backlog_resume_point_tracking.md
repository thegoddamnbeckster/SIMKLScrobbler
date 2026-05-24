# Backlog: Resume Point Tracking for Non-Library Streams

**Status:** Resolved (2026-05-20)  
**Priority:** Medium  
**Source:** Feature prompt (2026-05-19)

---

## Problem

For non-library streams (e.g. items played via the Umbrella addon using Real-Debrid),
the scrobbler has no mechanism to save or restore resume points. When a user stops
mid-way through a movie or episode and returns later, Kodi has no record of where they
left off because these streams never touch Kodi's local library database. This feature
exists natively for local library items but is completely absent for addon streams.

---

## Pre-Implementation Note

The prompt references source files by an older naming convention. Map as follows:

| Prompt refers to | Actual file |
|---|---|
| `engine.py` | `resources/lib/scrobbler.py` |
| `events.py` | `resources/lib/service.py` |
| `api_simkl.py` | `resources/lib/api.py` |
| `utils.py` | `resources/lib/utils.py` |
| `interface.py` | *(no direct equivalent — logic spread across service.py and auth_dialog.py)* |

**Read all source files before writing any code.**

---

## What Needs to Be Built

1. **Track position during playback** at regular intervals.

2. **On `onPlayBackStopped`** (interrupted before scrobble threshold): save the current
   position keyed by the item's media ID (prefer SIMKL ID, fall back to IMDB, then TMDB).
   Do **not** save on `onPlayBackEnded` — that means it played to completion.

3. **Storage:** local JSON file in Kodi's addon data directory:
   ```
   xbmcvfs.translatePath('special://userdata/addon_data/script.simkl/')
   ```

4. **On `onPlayBackStarted`**, after item detection: check if a saved resume point exists
   for the detected item. If one exists and the user has watched less than the scrobble
   threshold, prompt via a Kodi dialog asking if they want to resume. If yes, seek to the
   saved position.

5. **On successful scrobble** (item marked as watched): delete the resume entry for that
   item — it is no longer needed.

---

## Constraints

- Resume points must be keyed by **media ID, not file path or URL** — Real-Debrid URLs
  are temporary and change every session.
- Only non-library streams need this. Library items already have native Kodi resume
  handling. Distinguish by checking whether the item has a Kodi library `id` field.
- Do not attempt to write resume points into Kodi's `MyVideos.db` directly.
- Respect the existing `autoscrobble` setting — if autoscrobble is off, resume tracking
  should also be inactive.
- The "in progress" row in Kodi skins is a library widget and is explicitly **out of scope**
  — do not attempt to inject items into the Kodi library.

---

## Definition of Done

- Watching 40 minutes of a movie via Umbrella, stopping, then replaying it results in a
  dialog offering to resume from the 40-minute mark.
- Watching the same movie past the scrobble threshold clears the resume entry.
- No impact on existing behaviour for local library items.
