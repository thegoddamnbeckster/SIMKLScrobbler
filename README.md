# SIMKL Scrobbler for Kodi

Automatically track your Kodi watching activity to your [SIMKL](https://simkl.com) account. Movies and TV episodes are scrobbled in real-time as you watch, with bidirectional library sync, a rating system, and context menu integration.

This addon aims to provide the same quality of experience that the popular Trakt addon offers, but for SIMKL users.

**Current Version:** 7.3.1

## Features

### Real-Time Scrobbling
- Automatic detection of movies and TV episodes during playback
- Content identified via IMDb, TMDb, and TVDB IDs from your Kodi library
- Configurable watched threshold (default 70%) with automatic fallback to SIMKL's history API for items between 70-79%
- Start, pause, and stop events sent to SIMKL in real-time
- Multi-episode transition detection for binge-watching sessions
- Periodic progress updates every 15 minutes during long playback

### Bidirectional Library Sync
- **Export to SIMKL:** Send your Kodi watched history to SIMKL
- **Import from SIMKL:** Mark items as watched in Kodi based on your SIMKL history
- Sync triggers: on startup, on library update, or on a scheduled interval (1h, 6h, 12h, 24h)
- Delta sync detects changes since last sync to minimize API calls
- Optional dangerous mode to unmark items not found on SIMKL
- Per-type toggles for movies and TV shows in both directions

### Rating System
- 1-10 star rating dialog after watching movies
- Rating descriptions from "Train Wreck" (1) to "Legendary" (10)
- Displays your current SIMKL rating if the item was previously rated
- Ratings submitted via the SIMKL `/sync/ratings` endpoint
- Episode rating support is coded but disabled until SIMKL adds API support

### Context Menu Integration
Three companion addons add SIMKL actions to Kodi's library context menu:
- **SIMKL - Rating button** (`context.simkl.rate`) - Rate any movie, show, or episode
- **SIMKL - Toggle Watched** (`context.simkl.watched`) - Mark or unmark items on SIMKL
- **SIMKL - Sync to SIMKL** (`context.simkl.sync`) - Sync a single item immediately

### Exclusions
Control what gets scrobbled with granular exclusion settings:
- Exclude Live TV (`pvr://` sources)
- Exclude HTTP/HTTPS streaming sources
- Exclude plugin-triggered playback
- Exclude script-controlled playback
- Up to 5 custom path exclusions with cascading UI

### Localization Framework
- All user-facing strings centralized through `strings.py`
- Complete English localization in `strings.po`
- Framework ready for community translations

## Requirements

- **Kodi 19 (Matrix)** or later (Python 3)
- A free [SIMKL account](https://simkl.com)
- `script.module.requests` (bundled with Kodi)

## Installation

### From ZIP (Manual)
1. Download the latest release ZIP from the [Releases](https://github.com/thegoddamnbeckster/SIMKLScrobbler/releases) page
2. In Kodi, go to **Add-ons > Install from zip file**
3. Navigate to the downloaded ZIP and install
4. Optionally install the context menu addons from their respective ZIPs

### From Source
1. Clone this repository
2. Copy the `script.simkl` folder to your Kodi addons directory
3. Restart Kodi

## Setup

1. Open the addon settings (Add-ons > My Add-ons > Services > SIMKL Scrobbler > Configure)
2. Click **Authenticate with SIMKL**
3. A dialog will appear with a PIN code and QR code
4. Visit [simkl.com/pin](https://simkl.com/pin) and enter the PIN, or scan the QR code
5. The addon will detect authorization automatically and display your username

## Settings Overview

| Category | Key Settings |
|---|---|
| **Authentication** | Authenticate / Sign Out, connection status display |
| **Scrobbling** | Toggle movies/episodes, watched threshold (50-90%) |
| **Notifications** | Toggle scrobble notifications, duration, debug logging |
| **Exclusions** | Live TV, HTTP, plugin, script, and custom path exclusions |
| **Sync** | Startup sync, library update sync, scheduled interval, direction toggles |
| **Rating** | Toggle movie/show rating prompts, minimum view time, allow re-rating |

## Architecture

The addon follows the established Kodi service addon pattern used by the Trakt addon:

```
script.simkl/
├── addon.xml              # Addon metadata and dependencies
├── default.py             # Script entry point (settings buttons, context menu actions)
├── service.py             # Service entry point (background monitoring)
├── resources/
│   ├── settings.xml       # Kodi settings definition
│   ├── lib/
│   │   ├── api.py         # SIMKL API client (all HTTP communication)
│   │   ├── auth.py        # Authentication orchestration
│   │   ├── auth_dialog.py # WindowXMLDialog for PIN/QR auth flow
│   │   ├── exclusions.py  # Scrobble exclusion logic
│   │   ├── rating.py      # Rating dialog and submission
│   │   ├── scrobbler.py   # Core scrobbling engine
│   │   ├── service.py     # Main service loop, player/monitor classes
│   │   ├── strings.py     # Localization helper
│   │   ├── sync.py        # Bidirectional sync manager
│   │   └── utils.py       # Logging, settings, helpers
│   ├── skins/default/
│   │   ├── 720p/          # Dialog XML definitions
│   │   └── media/         # Star graphics, textures
│   └── language/
│       └── resource.language.en_gb/
│           └── strings.po # English localization strings
├── context.simkl.rate/    # Context menu: Rate on SIMKL
├── context.simkl.watched/ # Context menu: Toggle watched
└── context.simkl.sync/    # Context menu: Sync to SIMKL
```

The background service uses a dispatch queue pattern: `SimklPlayer` detects playback events and queues them, the main `SimklService` loop processes the queue, and `SimklScrobbler` handles the SIMKL API communication. Sync operations run in background threads to avoid blocking Kodi.

## SIMKL API Notes

- Authentication uses SIMKL's PIN-based OAuth flow (`/oauth/pin`)
- Scrobbling uses the `/scrobble/start`, `/scrobble/pause`, `/scrobble/stop` endpoints
- Watch history is managed via `/sync/history`
- Ratings use `/sync/ratings`
- SIMKL does not currently support rating individual episodes (only movies and shows)
- SIMKL does not store progress percentages for completed items (only for items under 80%)

## Known Limitations

- Show rating prompts are available but rated as shows (not individual episodes) because SIMKL's API does not support individual episode ratings
- The QR code in the authentication dialog is generated via a web API (qrserver.com); if the network request fails, only the PIN is shown
- The `auto_sync_interval` setting uses a `<select>` type which returns option values as strings rather than integers

## Contributing

Contributions are welcome. Please open an issue to discuss proposed changes before submitting a pull request.

## License

This project is licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html). See [LICENSE.txt](LICENSE.txt) for details.

## Credits

Developed by Claude.ai with assistance from Michael Beck.

Architectural patterns adapted from the [Trakt](https://github.com/trakt/script.trakt) Kodi addon.

SIMKL API documentation: [simkl.docs.apiary.io](https://simkl.docs.apiary.io/)
