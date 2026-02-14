# SIMKL Scrobbler v7.3.1 - Build Script
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$projectRoot = "W:\Scripts\SIMKL_Scrobbler"
$buildTemp = "$projectRoot\build_temp"
$addonFolder = "script.simkl"
$sourcePath = "$buildTemp\$addonFolder"
$outputZip = "C:\Temp\script.simkl-v7.3.1.zip"

Write-Host "======================================"
Write-Host " SIMKL Scrobbler v7.3.1 - Build"
Write-Host "======================================"

Write-Host ""
Write-Host "--- Preparing build_temp ---"
if (Test-Path $buildTemp) { Remove-Item $buildTemp -Recurse -Force; Write-Host "  Cleaned old build_temp" }
New-Item -ItemType Directory -Path $sourcePath -Force | Out-Null

$rootFiles = @("addon.xml","default.py","service.py","icon.png","fanart.jpg","LICENSE.txt","changelog.txt")
foreach ($f in $rootFiles) {
    $src = Join-Path $projectRoot $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $sourcePath $f); Write-Host "  Copied: $f" }
}
Copy-Item "$projectRoot\resources" "$sourcePath\resources" -Recurse -Force
Write-Host "  Copied: resources/"

Write-Host ""
Write-Host "--- Building ZIP ---"
if (Test-Path $outputZip) { Remove-Item $outputZip -Force; Write-Host "  Removed old ZIP" }

$zip = [System.IO.Compression.ZipFile]::Open($outputZip, 'Create')
$files = Get-ChildItem -Path $sourcePath -Recurse -File
$count = 0
foreach ($file in $files) {
    $relativePath = $file.FullName.Substring($buildTemp.Length + 1)
    $entryName = $relativePath.Replace("\", "/")
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
Write-Host "Version: 7.3.1"
Write-Host "Output:  $outputZip"
Write-Host "Files:   $count"
Write-Host "Size:    $zipSize KB"
