# Build context.simkl.rate v1.0.3

$SourceDir = "W:\Scripts\SIMKL_Scrobbler\context.simkl.rate"
$OutputDir = "C:\Temp"
$ZipPath = Join-Path $OutputDir "context.simkl.rate-v1.0.3.zip"

Write-Host "Building context.simkl.rate v1.0.3..." -ForegroundColor Cyan

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
Write-Host "v1.0.3 - Episode and Season Resolution" -ForegroundColor White
Write-Host "- Episodes: GetEpisodeDetails -> tvshowid -> scrobbler gets media_type=show" -ForegroundColor White
Write-Host "- Seasons:  GetSeasonDetails  -> tvshowid -> scrobbler gets media_type=show" -ForegroundColor White
Write-Host "- Error notification shown if resolution fails" -ForegroundColor White
Write-Host "- No scrobbler changes required (stays at v7.8.4)" -ForegroundColor White
