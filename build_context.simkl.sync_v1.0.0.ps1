# SIMKL Context Menu - Manual Sync Build Script v1.0.0
# Phase 6 Step 3: Manual Sync Context Menu

param(
    [string]$Version = "1.0.0",
    [string]$SourceDir = "W:\Scripts\SIMKL_Scrobbler\context.simkl.sync",
    [string]$OutputDir = "C:\Temp"
)

Write-Host "`nBuilding SIMKL Context Menu - Manual Sync v$Version..." -ForegroundColor Cyan

# Output filename
$ZipPath = Join-Path $OutputDir "context.simkl.sync-v$Version.zip"

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
        $EntryName = "context.simkl.sync/$($RelativePath.Replace('\', '/'))"
        
        # Add file to zip
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Zip, $File.FullName, $EntryName) | Out-Null
    }
} finally {
    $Zip.Dispose()
}

Write-Host "`nBUILD SUCCESSFUL!" -ForegroundColor Green
Write-Host "Location: $ZipPath" -ForegroundColor Yellow
Write-Host "`nv$Version - PHASE 6 STEP 3: Manual Sync Context Menu" -ForegroundColor Cyan
Write-Host "- NEW: Right-click → 'Sync to SIMKL now'" -ForegroundColor White
Write-Host "- Works on movies and episodes" -ForegroundColor White
Write-Host "- Syncs watched items immediately" -ForegroundColor White
Write-Host "- Shows success/failure notification" -ForegroundColor White
Write-Host "`nREQUIRES: script.simkl v6.9.0+ must be installed!" -ForegroundColor Yellow
Write-Host "`nInstallation:" -ForegroundColor Yellow
Write-Host "1. Install script.simkl v6.9.0 first" -ForegroundColor White
Write-Host "2. Install context.simkl.sync-v$Version.zip" -ForegroundColor White
Write-Host "3. Restart Kodi" -ForegroundColor White
Write-Host "4. Right-click any watched movie/episode → 'Sync to SIMKL now'" -ForegroundColor White
