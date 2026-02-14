# Build context.simkl.rate v1.0.1

$SourceDir = "W:\Scripts\SIMKL_Scrobbler\context.simkl.rate"
$OutputDir = "C:\Temp"
$ZipPath = Join-Path $OutputDir "context.simkl.rate-v1.0.1.zip"

Write-Host "Building context.simkl.rate v1.0.1..." -ForegroundColor Cyan

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

$AllFiles = Get-ChildItem -Path $SourceDir -Recurse -File

Write-Host "Packaging $($AllFiles.Count) files..." -ForegroundColor Yellow

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Zip = [System.IO.Compression.ZipFile]::Open($ZipPath, [System.IO.Compression.ZipArchiveMode]::Create)

foreach ($File in $AllFiles) {
    $RelativePath = $File.FullName.Substring($SourceDir.Length + 1)
    $ZipEntryName = "context.simkl.rate/" + $RelativePath.Replace('\', '/')
    
    $Entry = $Zip.CreateEntry($ZipEntryName, [System.IO.Compression.CompressionLevel]::Optimal)
    $EntryStream = $Entry.Open()
    $FileStream = [System.IO.File]::OpenRead($File.FullName)
    $FileStream.CopyTo($EntryStream)
    $FileStream.Close()
    $EntryStream.Close()
}

$Zip.Dispose()

Write-Host ""
Write-Host "BUILD SUCCESSFUL!" -ForegroundColor Green
Write-Host "Location: $ZipPath" -ForegroundColor Green
Write-Host ""
Write-Host "v1.0.1 - CRITICAL FIX!" -ForegroundColor White
Write-Host "- FIXED: Uses ListItem.DBTYPE instead of Container.Content()" -ForegroundColor White
Write-Host "- NOW WORKS: In progress, recently added, all views!" -ForegroundColor White
Write-Host "- Added logging for debugging" -ForegroundColor White

