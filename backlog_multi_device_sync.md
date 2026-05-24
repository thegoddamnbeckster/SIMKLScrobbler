# Backlog: Multi-Device Watched Status Sync (SIMKL → Kodi)

**Status:** Resolved (2026-05-19)  
**Priority:** Medium  
**Source:** Feature prompt (2026-05-19)

---

## Problem

A user watches a movie on one Kodi install. The scrobbler marks it as watched in SIMKL.
On a second Kodi install running the same scrobbler and logged into the same SIMKL account,
that movie still shows as unwatched. SIMKL has the correct data — it just never gets
written back to the second Kodi instance's local library.

The reverse is also a problem: if a user resets their watch status in SIMKL (e.g. to
rewatch an entire series, season, or episode), Kodi has no way of knowing and continues
to show those items as watched. SIMKL is the source of truth — Kodi should mirror it in
both directions.

---

## Pre-Implementation Note: Audit Existing Sync Before Writing Code

The project plan indicates that SIMKL → Kodi import sync was implemented in Phase 3B and
significantly reworked in v7.5.7 (fixing the `date_from` bug on the watching endpoint).
`resources/lib/sync.py` already contains bidirectional sync logic.

**Before implementing anything in this backlog item, audit `sync.py` to determine:**
- Whether `sync_from_simkl()` already calls `VideoLibrary.SetMovieDetails` /
  `VideoLibrary.SetEpisodeDetails` to write playcount back into the Kodi library.
- If yes, identify what gap this prompt is actually describing (e.g. missing "Sync now"
  button, auto-interval not configurable, or sync not covering all item types).
- If no, proceed with the full implementation below.

The prompt also references source files by an older naming convention:

| Prompt refers to | Actual file |
|---|---|
| `engine.py` | `resources/lib/scrobbler.py` |
| `events.py` | `resources/lib/service.py` |
| `api_simkl.py` | `resources/lib/api.py` |
| `utils.py` | `resources/lib/utils.py` |
| `interface.py` | *(no direct equivalent)* |

**Read `resources/lib/sync.py`, `resources/settings.xml`, and `default.py` first.**

---

## What Needs to Be Built

1. **Pull watched history from SIMKL** on addon startup and on a configurable interval
   (default 24 hours, user-adjustable via settings).

2. For each watched item returned by SIMKL, **search Kodi's local library** for a matching
   item using available IDs (IMDB, TMDB, TVDB) via:
   - `VideoLibrary.GetMovies` with `uniqueid` and `playcount` properties
   - `VideoLibrary.GetEpisodes` with `uniqueid` and `playcount` properties

3. **Mirror SIMKL's watched state into the Kodi library** for every matched item:
   - If SIMKL says **watched** and Kodi shows `playcount = 0`: set `playcount: 1`
   - If SIMKL says **unwatched** (user reset it) and Kodi shows `playcount > 0`: set `playcount: 0`
   - Use `VideoLibrary.SetMovieDetails` / `VideoLibrary.SetEpisodeDetails` accordingly.
   - SIMKL manages the overall playcount history; this sync only cares about the
     binary watched/unwatched state Kodi displays to the user.

4. **One-way pull only** — the existing push direction (Kodi → SIMKL via scrobbler)
   handles the other direction. Do not sync in reverse during this process.

5. **"Sync now" option** in the addon settings or context menu for manual on-demand sync.

6. **Log all sync activity** clearly: what was found, what was updated, what was skipped.

---

## Constraints

- **SIMKL is the source of truth** — Kodi's watched state should mirror SIMKL's current
  state exactly, including resets. If SIMKL says unwatched, Kodi gets set to unwatched.
- Only update items that **exist in Kodi's local library** — do not add items or handle
  non-library streams.
- SIMKL manages the overall playcount history. This sync only sets Kodi's binary
  watched/unwatched display state (`playcount` 0 or 1) — do not attempt to replicate
  SIMKL's rewatch counts into Kodi's playcount field.
- If the SIMKL API call fails, log the failure and skip the sync cycle gracefully without
  surfacing an error to the user unless it has failed repeatedly.
- Add a setting to **enable/disable automatic sync independently** of the existing
  `autoscrobble` setting.
- Be mindful of SIMKL API rate limits — fetch history in as few calls as possible and
  cache the result for the sync interval duration.

---

## Definition of Done

- Watching a movie on one Kodi install causes it to appear as watched on a second Kodi
  install running the same addon and SIMKL account, within one sync interval.
- Resetting a show/season/episode as unwatched in SIMKL causes the corresponding Kodi
  library items to appear as unwatched on the next sync cycle.
- Manual "Sync now" works and updates the library immediately in both directions.
- No existing scrobble behaviour is affected.
