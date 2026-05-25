# SIMKL Context Menu

**Version:** 1.0.3
**Type:** Kodi Context Menu Addon
**Requires:** script.simkl.scrobbler v7.8.4+

> **Upgrading from context.simkl.rate?** Uninstall that addon first — Kodi treats
> them as different addons because the ID changed.

## Purpose

Adds SIMKL actions to the right-click context menu when browsing movies, TV shows,
seasons, or episodes in Kodi.

## Current Actions

### Rate on SIMKL
Right-click any media item → **Rate on SIMKL**

- Works on movies, TV shows, seasons, and episodes
- Episodes and seasons automatically resolve to their parent show (SIMKL rates shows,
  not individual episodes)
- Delegates to `script.simkl.scrobbler` which shows the 1-10 star rating dialog

## Planned Actions (Backlog)

The following actions are planned for a future release. They will be added as
additional context menu items in this addon — no separate addon required.

### Toggle Watched on SIMKL
Mark or unmark an item as watched on SIMKL directly from the context menu.
Requires `action=togglewatched` to be implemented in `script.simkl.scrobbler`.

### Sync to SIMKL
Trigger a sync of a single item to SIMKL without running a full library sync.
Requires `action=sync` per-item to be implemented in `script.simkl.scrobbler`.

## Installation

1. Install main addon: `script.simkl.scrobbler` v7.8.4 or higher
2. Install this context addon: `context.simkl`
3. Restart Kodi
4. Right-click any media item -> "Rate on SIMKL" appears

## How It Works

This is a **standalone context menu addon** (required by Kodi architecture):
- Detects media type and database ID from the selected item
- For **episodes** and **seasons**: resolves to the parent show via JSON-RPC before delegating
- Calls `script.simkl.scrobbler` with `action=rate` and the resolved type/ID
- Main addon handles authentication, API calls, and the rating dialog

SIMKL supports only movie and show ratings — not individual episodes or seasons.
This addon handles the translation transparently so you always rate the correct entity.

## Technical Details

### Media Type Detection
Uses `ListItem.DBTYPE` instead of `Container.Content()`:
- **DBTYPE**: Works everywhere (library, in progress, recently added, all views)
- **Container.Content**: Only works in main library views

### Episode and Season Resolution
- **Episode**: `GetEpisodeDetails -> tvshowid` -> calls scrobbler with `media_type=show, dbid=<tvshowid>`
- **Season**: `GetSeasonDetails -> tvshowid` -> calls scrobbler with `media_type=show, dbid=<tvshowid>`
- **Movie / Show**: passed through directly, no resolution needed

### Visibility Condition
```xml
String.IsEqual(ListItem.dbtype,movie) |
String.IsEqual(ListItem.dbtype,tvshow) |
String.IsEqual(ListItem.dbtype,season) |
String.IsEqual(ListItem.dbtype,episode)
```

## Files

```
context.simkl/
├── addon.py                    # Main script (detection, resolution, delegation)
├── addon.xml                   # Addon metadata + context menu registration
├── changelog.txt               # Version history
├── icon.png                    # Addon icon
├── LICENSE.txt                 # MIT License
├── README.md                   # This file
└── resources/
    └── language/
        └── resource.language.en_gb/
            └── strings.po      # UI strings (label ID 32000)
```

## Logging

All operations logged to kodi.log with version prefix:
```
[context.simkl v1.0.3] CONTEXT MENU TRIGGERED
[context.simkl v1.0.3] ListItem.DBTYPE = 'episode'
[context.simkl v1.0.3] Detected media_type = 'episode'
[context.simkl v1.0.3] DBID = '8821'
[context.simkl v1.0.3] Episode DBID 8821 -> show 'Breaking Bad' (tvshowid=42)
[context.simkl v1.0.3] Executing: RunScript(script.simkl.scrobbler,action=rate,media_type=show,dbid=42)
```

## Changelog

### v1.0.3 (2026-05-25)
- **RENAMED:** `context.simkl.rate` -> `context.simkl` — generic name to accommodate future actions
- **FIXED:** Episodes now resolve to parent show via `GetEpisodeDetails -> tvshowid` before calling scrobbler
- **FIXED:** Seasons now resolve to parent show via `GetSeasonDetails -> tvshowid` before calling scrobbler
- **FIXED:** Error notification shown to user when resolution fails
- **FIXED:** README and dependency corrected (`script.simkl` -> `script.simkl.scrobbler`)
- **Architecture:** All type resolution happens in this addon; scrobbler always receives movie or show

### v1.0.1 (2025-12-27)
- **FIXED:** Media type detection now uses ListItem.DBTYPE (works in all views)
- **IMPROVED:** Professional logging with version tracking
- **IMPROVED:** Error handling with try/except and traceback

### v1.0.0 (2025-12-27)
- Initial release
- Context menu registration
- Basic media type detection

## Credits

**Created by:** Claude.ai with assistance from Michael Beck
**License:** MIT
**Project:** SIMKL Scrobbler for Kodi
