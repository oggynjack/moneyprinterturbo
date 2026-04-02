$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Write-Host "Fetching latest ImageMagick release from GitHub..."
$release = Invoke-RestMethod -Uri "https://api.github.com/repos/ImageMagick/ImageMagick/releases/latest" -UseBasicParsing
$asset = $release.assets | Where-Object { $_.name -like "*portable-Q16-x64.zip*" } | Select-Object -First 1

if (-not $asset) {
    Write-Host "Could not find ImageMagick portable zip."
    exit 1
}

$url = $asset.browser_download_url
Write-Host "Downloading $url ..."
Invoke-WebRequest -Uri $url -OutFile "imagemagick.zip" -UseBasicParsing

Write-Host "Extracting ImageMagick..."
Expand-Archive -Path "imagemagick.zip" -DestinationPath "imagemagick" -Force
Remove-Item "imagemagick.zip"

Write-Host "ImageMagick has been successfully downloaded and extracted."
Write-Host "Starting WebUI..."
.\webui.bat
