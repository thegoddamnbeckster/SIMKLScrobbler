# SIMKL Scrobbler (`script.simkl.scrobbler`)

Kodi addon replicating Trakt addon functionality for SIMKL. Tracks and syncs TV/movie
watch history, ratings, and library state between Kodi and SIMKL's API.

- **Repo:** github.com/thegoddamnbeckster/SIMKLScrobbler
- **Current version:** 7.5.6 (source of truth: `addon.xml`)
- **Target:** Public distribution, professional code standards

---

## Environment

- **Source tree:** `W:\Scripts\SIMKL_Scrobbler`
- **Build output:** `C:\Temp\script.simkl.scrobbler-{version}.zip`
- **Kodi install:** Microsoft Store (non-standard AppData path)
- **Shell:** PowerShell primary, Git + GitHub CLI (`gh`)

---

## Build

```powershell
# From W:\Scripts\SIMKL_Scrobbler
.\build.ps1
```

- `build.ps1` reads version automatically from `addon.xml` — no manual version arg needed
- Output goes to `C:\Temp\`
- **CRITICAL:** Use `.NET System.IO.Compression.ZipArchive` with **forward-slash** path separators
- **NEVER** use `Compress-Archive` — it writes backslashes that break Kodi installations

---

## Release Pipeline

```powershell
# 1. Commit and tag
git add .
git commit -m "vX.Y.Z: description"
git tag vX.Y.Z
git push && git push --tags

# 2. Write release notes to temp file (avoid shell escaping issues)
# Write to C:\Temp\release_notes.md first

# 3. Create/update release with notes file
gh release create vX.Y.Z "C:\Temp\script.simkl.scrobbler-X.Y.Z.zip" --notes-file "C:\Temp\release_notes.md"
# or to edit existing:
gh release edit vX.Y.Z --notes-file "C:\Temp\release_notes.md"
```

**Shell escaping rule:** Never embed multiline content in `-Command "..."`. Write to a temp
file first, then reference the file path in a separate command.

---

## Key Files

| File | Purpose |
|---|---|
| `addon.xml` | Version source of truth, addon metadata |
| `service.py` | `SimklMonitor` entry point, scan-awareness callbacks |
| `rating.py` | Rating dialog — watch for mixed f-string/`.format()` patterns |
| `changelog.txt` | Must be updated in sync with `addon.xml` |
| `build.ps1` | Generic build script, reads version from `addon.xml` |

---

## Version Bumping

Version must be updated **in coordination** across:
1. `addon.xml`
2. All Python modules (use dynamic `{__version__}` references, not hardcoded strings)
3. `changelog.txt`
4. Build script output (automatic if reading from `addon.xml`)

---

## Code Conventions

- **Public project** — fully PG-rated: no profanity, no crude content in comments, logs, or output
- Professional style, naming, and documentation throughout
- Comprehensive error handling
- **Unicode:** Only in `.md` files. Never in code, logs, screen output, or executables — keep ASCII-compatible

---

## Key Technical Gotchas

### Android vs Windows bytecode
Cached `.pyc` files on Windows can mask Python syntax errors that surface as **silent import
failures** on Android (e.g. NVIDIA Shield running fresh parsing). Always test clean.

### Kodi settings cache
`Addon()` singleton caches settings. Create a **fresh `Addon()` instance** when reading
settings that may have changed at runtime.

### File locks
When PowerShell `Set-Content` silently fails due to file locks, use `edit_block` instead.
Use `Remove-Item -Force` before writing locked files.

### Context menus
Must be implemented as **separate standalone addons** (same pattern as `script.trakt`),
not integrated into the main service addon.

### Rating dialog architecture
Three-layer system: grey star images + gold star images with Python-controlled visibility +
transparent button overlays. Eliminates radiobutton toggle conflicts.

---

## SIMKL API

- **Docs:** simkl.docs.apiary.io
- **Key endpoints:**
  - `/sync/activities` — incremental sync with `date_from`
  - `/sync/ratings` — bidirectional rating sync
  - `/sync/ratings/remove` — unrate
  - `/scrobble/start`, `/scrobble/pause`, `/scrobble/stop`
- **Known limitation:** SIMKL does not store progress percentages for completed items (>80%) —
  only for in-progress items. Platform limitation, not an addon bug.
- **Rate limiting:** Implement exponential backoff for 429/5xx responses

---

## Reference Implementation

`script.trakt` (Trakt addon) is the architectural template. Consult it for patterns
not yet implemented in this addon.

---

## Useful Links

- SIMKL Discord: discord.gg/u89XfYn
- SIMKL support: support@simkl.com
