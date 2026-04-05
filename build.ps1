# SIMKL Scrobbler - Build Script
# Reads version from addon.xml automatically. Outputs ZIP to C:\Temp\.
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$addonXml    = Join-Path $projectRoot "addon.xml"

# Read version from addon.xml
$xml     = [xml](Get-Content $addonXml -Encoding UTF8)
$version = $xml.addon.version
if (-not $version) {
    Write-Error "Could not read version from addon.xml"
    exit 1
}

$buildTemp   = Join-Path $projectRoot "build_temp"
$addonFolder = "script.simkl"
$sourcePath  = Join-Path $buildTemp $addonFolder
$outputZip   = "C:\Temp\script.simkl.scrobbler-$version.zip"

Write-Host "======================================"
Write-Host " SIMKL Scrobbler v$version - Build"
Write-Host "======================================"

# Step 1: Prepare build_temp
Write-Host ""
Write-Host "--- Preparing build_temp ---"

if (Test-Path $buildTemp) {
    Remove-Item $buildTemp -Recurse -Force
    Write-Host "  Cleaned old build_temp"
}
New-Item -ItemType Directory -Path $sourcePath -Force | Out-Null

# Copy root addon files
$rootFiles = @("addon.xml","default.py","service.py","icon.png","fanart.jpg","LICENSE.txt","changelog.txt")
foreach ($f in $rootFiles) {
    $src = Join-Path $projectRoot $f
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $sourcePath $f)
        Write-Host "  Copied: $f"
    }
}

# Copy resources directory (excluding __pycache__)
Copy-Item "$projectRoot\resources" "$sourcePath\resources" -Recurse -Force
Get-ChildItem -Path $sourcePath -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
Write-Host "  Copied: resources/ (cleaned __pycache__)"
Write-Host "  build_temp ready."

# Step 2: Create ZIP
Write-Host ""
Write-Host "--- Building ZIP ---"

if (Test-Path $outputZip) {
    Remove-Item $outputZip -Force
    Write-Host "  Removed old ZIP"
}

$zip   = [System.IO.Compression.ZipFile]::Open($outputZip, 'Create')
$files = Get-ChildItem -Path $sourcePath -Recurse -File
$count = 0

foreach ($file in $files) {
    $relativePath = $file.FullName.Substring($buildTemp.Length + 1)
    $entryName    = $relativePath.Replace("\", "/")
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $entryName, 'Optimal') | Out-Null
    Write-Host "  + $entryName"
    $count++
}

$zip.Dispose()

$zipInfo = Get-Item $outputZip
$zipSize = [math]::Round($zipInfo.Length / 1024, 2)

Write-Host ""
Write-Host "======================================"
Write-Host " BUILD COMPLETE"
Write-Host "======================================"
Write-Host "Version: $version"
Write-Host "Output:  $outputZip"
Write-Host "Files:   $count"
Write-Host "Size:    $zipSize KB"

# Cleanup build_temp
Remove-Item $buildTemp -Recurse -Force
Write-Host "  Cleaned build_temp"
