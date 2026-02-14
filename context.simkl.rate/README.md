# SIMKL Context Menu - Rating Button

**Version:** 1.0.1  
**Type:** Kodi Context Menu Addon  
**Requires:** script.simkl v6.7.0+

## Purpose

Adds "Rate on SIMKL" option to the context menu when right-clicking movies, TV shows, seasons, or episodes in Kodi.

## Installation

1. Install main addon: `script.simkl` v6.7.0 or higher
2. Install this context addon: `context.simkl.rate`
3. Restart Kodi
4. Right-click any media item → "Rate on SIMKL" appears

## How It Works

This is a **standalone context menu addon** (required by Kodi architecture):
- Detects media type and database ID from the selected item
- Calls `script.simkl` with `action=rate` parameter
- Main addon handles authentication, API calls, rating dialog

## Technical Details

### Media Type Detection
Uses `ListItem.DBTYPE` instead of `Container.Content()`:
- **DBTYPE**: Works everywhere (library, in progress, recently added, all views)
- **Container.Content**: Only works in main library views

### Supported Media Types
- Movies (`dbtype=movie`)
- TV Shows (`dbtype=tvshow`)
- Seasons (`dbtype=season`)
- Episodes (`dbtype=episode`)

### Visibility Condition
```xml
String.IsEqual(ListItem.dbtype,movie) | 
String.IsEqual(ListItem.dbtype,tvshow) | 
String.IsEqual(ListItem.dbtype,season) | 
String.IsEqual(ListItem.dbtype,episode)
```

## Files

```
context.simkl.rate/
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
[context.simkl.rate v1.0.1] CONTEXT MENU TRIGGERED
[context.simkl.rate v1.0.1] ListItem.DBTYPE = 'movie'
[context.simkl.rate v1.0.1] Detected media_type = 'movie'
[context.simkl.rate v1.0.1] DBID = '2566'
[context.simkl.rate v1.0.1] Executing: RunScript(script.simkl,action=rate,media_type=movie,dbid=2566)
```

## Changelog

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
**Project:** SIMKL Scrobbler for Kodi (Project 4)
