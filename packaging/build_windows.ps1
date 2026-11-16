$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $ProjectRoot
try {
    python -m PyInstaller --clean --noconfirm packaging/VideoDownloadManager.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    if (-not (Test-Path "dist/VideoDownloadManager/VideoDownloadManager.exe")) {
        throw "PyInstaller did not produce the expected application executable."
    }
    Write-Host "Built dist/VideoDownloadManager/VideoDownloadManager.exe"
} finally {
    Pop-Location
}
