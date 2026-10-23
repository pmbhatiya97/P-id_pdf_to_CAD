param([string]$Python = 'python', [switch]$SkipExe)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot 'build\pyinstaller-cache'
# Qt on Windows imports the OS ICU API. A Poppler ICU found on PATH exports
# a different, version-suffixed API and must not be bundled in its place.
$env:PATH = (($env:PATH -split ';') | Where-Object { $_ -notmatch '(?i)[\\/]poppler([\\/]|$)' }) -join ';'
if (Test-Path -LiteralPath (Join-Path $projectRoot '.runtime')) {
    $env:PYTHONPATH = @($projectRoot, (Join-Path $projectRoot '.runtime'), (Join-Path $projectRoot '.runtime\win32'), (Join-Path $projectRoot '.runtime\win32\lib'), (Join-Path $projectRoot '.runtime\pywin32_system32')) -join ';'
}
if (-not $SkipExe) {
    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name LineaForge --paths $projectRoot --collect-submodules lineaforge --collect-data ezdxf --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module matplotlib --add-data 'docs/dataset_crops;docs/dataset_crops' scripts/desktop_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
    & $Python scripts/validate_qt_runtime.py (Join-Path $projectRoot 'dist\LineaForge\_internal')
    if ($LASTEXITCODE -ne 0) { throw 'Qt runtime dependency validation failed' }
    & $Python -m PyInstaller --noconfirm --clean --onefile --console --name LineaForgeCadServer --distpath dist/LineaForge --paths $projectRoot --exclude-module PySide6 --exclude-module cv2 --exclude-module matplotlib scripts/cad_server_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'MCP helper build failed' }
}
& $Python scripts/package.py
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
$inno = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($inno -and -not $SkipExe) {
    & $inno.Source installer/LineaForge.iss
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed' }
} else {
    Write-Output 'Inno Setup unavailable or executable skipped: installer script supplied; setup.exe not built.'
}
