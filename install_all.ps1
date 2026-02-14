# SIMKL Addon Complete Installation Script
# Handles uninstall, build, and install in one go
# For lazy piggies who can't follow multi-step instructions

param(
    [string]$Version = "6.9.0",
    [switch]$SkipKodiKill = $false
)

$ScriptRoot = "W:\Scripts\SIMKL_Scrobbler"
$TempDir = "C:\Temp"
$KodiAddonsPath = "C:\Users\mbeck\AppData\Local\Packages\XBMCFoundation.Kodi_4n2hpmxwrvr6p\LocalCache\Roaming\Kodi\addons"

Write-Host "`n================================================" -ForegroundColor Cyan
Write-Host "  SIMKL ADDON COMPLETE INSTALLATION v$Version" -ForegroundColor Cyan
Write-Host "================================================`n" -ForegroundColor Cyan

# STEP 1: Stop Kodi
if (-not $SkipKodiKill) {
    Write-Host "[1/6] Stopping Kodi..." -ForegroundColor Yellow
    $KodiProcess = Get-Process -Name "Kodi" -ErrorAction SilentlyContinue
    if ($KodiProcess) {
        Stop-Process -Name "Kodi" -Force
        Start-Sleep -Seconds 2
        Write-Host "      Kodi stopped.`n" -ForegroundColor Green
    } else {
        Write-Host "      Kodi not running.`n" -ForegroundColor Gray
    }
} else {
    Write-Host "[1/6] Skipping Kodi kill (manual close required!)" -ForegroundColor Yellow
}

# STEP 2: Uninstall old addons
Write-Host "[2/7] Removing old addons..." -ForegroundColor Yellow
$AddonsToRemove = @("script.simkl", "context.simkl.rate", "context.simkl.watched", "context.simkl.sync", "context.simkl.test")
foreach ($Addon in $AddonsToRemove) {
    $AddonPath = Join-Path $KodiAddonsPath $Addon
    if (Test-Path $AddonPath) {
        Remove-Item $AddonPath -Recurse -Force
        Write-Host "      ✓ Removed: $Addon" -ForegroundColor Green
    }
}
Write-Host ""

# STEP 3: Build main addon
Write-Host "[3/7] Building script.simkl v$Version..." -ForegroundColor Yellow
$BuildScript = Join-Path $ScriptRoot "build_v$Version.ps1"
if (-not (Test-Path $BuildScript)) {
    Write-Host "      ERROR: Build script not found!" -ForegroundColor Red
    exit 1
}
& powershell.exe -ExecutionPolicy Bypass -File $BuildScript | Out-Null
Write-Host "      ✓ Built successfully`n" -ForegroundColor Green

# STEP 4: Build context.simkl.watched
Write-Host "[4/7] Building context.simkl.watched v1.0.0..." -ForegroundColor Yellow
$ContextBuildScript = Join-Path $ScriptRoot "build_context.simkl.watched_v1.0.0.ps1"
& powershell.exe -ExecutionPolicy Bypass -File $ContextBuildScript | Out-Null
Write-Host "      ✓ Built successfully`n" -ForegroundColor Green

# STEP 5: Build context.simkl.sync
Write-Host "[5/7] Building context.simkl.sync v1.0.0..." -ForegroundColor Yellow
$SyncBuildScript = Join-Path $ScriptRoot "build_context.simkl.sync_v1.0.0.ps1"
& powershell.exe -ExecutionPolicy Bypass -File $SyncBuildScript | Out-Null
Write-Host "      ✓ Built successfully`n" -ForegroundColor Green

# STEP 6: Extract addons to Kodi
Write-Host "[6/7] Installing addons to Kodi..." -ForegroundColor Yellow

# Extract main addon
$MainZip = Join-Path $TempDir "script.simkl-v$Version.zip"
$MainDest = Join-Path $KodiAddonsPath "script.simkl"
Expand-Archive -Path $MainZip -DestinationPath $KodiAddonsPath -Force
Write-Host "      ✓ Installed script.simkl" -ForegroundColor Green

# Extract context.simkl.watched
$ContextZip = Join-Path $TempDir "context.simkl.watched-v1.0.0.zip"
Expand-Archive -Path $ContextZip -DestinationPath $KodiAddonsPath -Force
Write-Host "      ✓ Installed context.simkl.watched" -ForegroundColor Green

# Extract context.simkl.sync
$SyncZip = Join-Path $TempDir "context.simkl.sync-v1.0.0.zip"
Expand-Archive -Path $SyncZip -DestinationPath $KodiAddonsPath -Force
Write-Host "      ✓ Installed context.simkl.sync" -ForegroundColor Green

# Also install rating context menu if exists
$RatingZip = Join-Path $TempDir "context.simkl.rate-v1.0.1.zip"
if (Test-Path $RatingZip) {
    Expand-Archive -Path $RatingZip -DestinationPath $KodiAddonsPath -Force
    Write-Host "      ✓ Installed context.simkl.rate" -ForegroundColor Green
}
Write-Host ""

# STEP 7: Done
Write-Host "[7/7] Installation complete!" -ForegroundColor Green
Write-Host "`n================================================" -ForegroundColor Cyan
Write-Host "  NEXT STEPS:" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "1. Launch Kodi" -ForegroundColor White
Write-Host "2. Wait for it to fully load" -ForegroundColor White
Write-Host "3. Right-click any movie/episode" -ForegroundColor White
Write-Host "4. Look for 'Sync to SIMKL now'" -ForegroundColor White
Write-Host "5. Test syncing a watched item" -ForegroundColor White
Write-Host "`n" -ForegroundColor White
