# SIMKL Context Menu - Toggle Watched Button

**Version:** 1.0.0  
**Type:** Kodi Context Menu Addon  
**Requires:** script.simkl v6.7.1+

## Purpose

Adds "Toggle watched on SIMKL" option to the context menu when right-clicking movies, TV shows, seasons, or episodes in Kodi.

## Installation

1. Install main addon: `script.simkl` v6.7.1 or higher
2. Install this context addon: `context.simkl.watched`
3. Restart Kodi
4. Right-click any media item → "Toggle watched on SIMKL" appears

## How It Works

This is a **standalone context menu addon** (required by Kodi architecture):
- Detects media type and database ID from the selected item
- Calls `script.simkl` with `action=togglewatched` parameter
- Main addon queries current watched state from SIMKL
- Toggles to opposite state (watched ↔ unwatched)
- Syncs immediately to SIMKL
- Shows success notification

## Features

- **Smart Toggle**: Checks current state and switches to opposite
- **Immediate Sync**: Changes sync to SIMKL instantly
- **All Media Types**: Works on movies, TV shows, seasons, episodes
- **All Views**: Library, in progress, recently added, etc.
- **Clear Feedback**: Success/error notifications

## Supported Media Types

- Movies (`dbtype=movie`)
- TV Shows (`dbtype=tvshow`)
- Seasons (`dbtype=season`)
- Episodes (`dbtype=episode`)

## Visibility Condition

```xml
String.IsEqual(ListItem.dbtype,movie) | 
String.IsEqual(ListItem.dbtype,tvshow) | 
String.IsEqual(ListItem.dbtype,season) | 
String.IsEqual(ListItem.dbtype,episode)
```

## Files

```
context.simkl.watched/
├── addon.py                    # Main script (detection + delegation)
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
[context.simkl.watched v1.0.0] CONTEXT MENU TRIGGERED
[context.simkl.watched v1.0.0] ListItem.DBTYPE = 'movie'
[context.simkl.watched v1.0.0] Detected media_type = 'movie'
[context.simkl.watched v1.0.0] DBID = '2566'
[context.simkl.watched v1.0.0] Executing: RunScript(script.simkl,action=togglewatched,media_type=movie,dbid=2566)
```

## Changelog

### v1.0.0 (2025-12-27)
- Initial release
- Toggle watched status on SIMKL
- Immediate sync
- Works in all views

## Credits

**Created by:** Claude.ai with assistance from Michael Beck  
**License:** MIT  
**Project:** SIMKL Scrobbler for Kodi (Project 4)
