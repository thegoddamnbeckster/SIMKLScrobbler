# SIMKL Context Menu - Toggle Watched Build Script v1.0.0
# Phase 6 Step 2: Toggle Watched Context Menu

param(
    [string]$Version = "1.0.0",
    [string]$SourceDir = "W:\Scripts\SIMKL_Scrobbler\context.simkl.watched",
    [string]$OutputDir = "C:\Temp"
)

Write-Host "`nBuilding SIMKL Context Menu - Toggle Watched v$Version..." -ForegroundColor Cyan

# Output filename
$ZipPath = Join-Path $OutputDir "context.simkl.watched-v$Version.zip"

# Remove old zip if exists
if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

# Create zip using .NET (to get forward slashes required by Kodi)
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Zip = [System.IO.Compression.ZipFile]::Open($ZipPath, 'Create')

try {
    # Get all files recursively
    $Files = Get-ChildItem -Path $SourceDir -Recurse -File
    
    Write-Host "Packaging $($Files.Count) files..." -ForegroundColor Yellow
    
    foreach ($File in $Files) {
        # Calculate relative path from source directory
        $RelativePath = $File.FullName.Substring($SourceDir.Length + 1)
        
        # Replace backslashes with forward slashes (required by Kodi)
        $EntryName = "context.simkl.watched/$($RelativePath.Replace('\', '/'))"
        
        # Add file to zip
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Zip, $File.FullName, $EntryName) | Out-Null
    }
} finally {
    $Zip.Dispose()
}

Write-Host "`nBUILD SUCCESSFUL!" -ForegroundColor Green
Write-Host "Location: $ZipPath" -ForegroundColor Yellow
Write-Host "`nv$Version - PHASE 6 STEP 2: Toggle Watched Context Menu" -ForegroundColor Cyan
Write-Host "- NEW: Right-click → 'Toggle watched on SIMKL'" -ForegroundColor White
Write-Host "- Works on movies and episodes" -ForegroundColor White
Write-Host "- Toggles Kodi watched state immediately" -ForegroundColor White
Write-Host "- Sync to SIMKL happens during playback or manual sync" -ForegroundColor White
Write-Host "`nREQUIRES: script.simkl v6.8.0+ must be installed!" -ForegroundColor Yellow
Write-Host "`nInstallation:" -ForegroundColor Yellow
Write-Host "1. Install script.simkl v6.8.0 first" -ForegroundColor White
Write-Host "2. Install context.simkl.watched-v$Version.zip" -ForegroundColor White
Write-Host "3. Restart Kodi" -ForegroundColor White
Write-Host "4. Right-click any movie/episode → 'Toggle watched on SIMKL'" -ForegroundColor White
