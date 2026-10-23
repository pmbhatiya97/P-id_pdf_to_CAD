param([string]$Python = 'python', [switch]$SkipFrontend)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$buildStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$workDirectory = Join-Path $projectRoot ('build\web-' + $buildStamp)
$env:PYINSTALLER_CONFIG_DIR = Join-Path $workDirectory 'cache'
$releaseDirectory = Join-Path $projectRoot ('dist\web-' + $buildStamp)
$portableDirectory = Join-Path $releaseDirectory 'LineaForgeWeb'
$sslDataArgs = @()
$sslPolicyFile = & $Python -c "import os,ssl; print(os.path.join(os.path.dirname(ssl.__file__),'codex-rejected-cas.pem'))"
if (Test-Path -LiteralPath $sslPolicyFile) {
    # Preserve the managed runtime's SSL policy; never bypass verification.
    $sslDataArgs = @('--add-data', ($sslPolicyFile + ';.'))
}
$env:PATH = (($env:PATH -split ';') | Where-Object { $_ -notmatch '(?i)[\\/]poppler([\\/]|$)' }) -join ';'
if (Test-Path -LiteralPath '.webruntime') {
    $env:PYTHONPATH = @($projectRoot,(Join-Path $projectRoot '.webruntime-secure'),(Join-Path $projectRoot '.webruntime'),(Join-Path $projectRoot '.runtime'),(Join-Path $projectRoot '.runtime\win32'),(Join-Path $projectRoot '.runtime\win32\lib'),(Join-Path $projectRoot '.runtime\pywin32_system32')) -join ';'
}
if (-not $SkipFrontend) {
    Push-Location -LiteralPath (Join-Path $projectRoot 'web')
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'React build failed' }
    } finally { Pop-Location }
}
& $Python -m PyInstaller --noconfirm --onedir --console --name LineaForgeWeb --workpath $workDirectory --distpath $releaseDirectory --paths $projectRoot --collect-submodules lineaforge --collect-data ezdxf --exclude-module PySide6 --exclude-module matplotlib --exclude-module scipy --exclude-module pandas --exclude-module pytest @sslDataArgs --add-data 'web/dist;web/dist' scripts/web_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Web runtime build failed' }
& $Python -m PyInstaller --noconfirm --onefile --console --name LineaForgeCadBridge --workpath $workDirectory --distpath $portableDirectory --paths $projectRoot --exclude-module PySide6 --exclude-module cv2 --exclude-module matplotlib --exclude-module scipy --exclude-module pandas --exclude-module pytest @sslDataArgs scripts/cad_bridge_entry.py
if ($LASTEXITCODE -ne 0) { throw 'Windows CAD bridge build failed' }
Copy-Item -LiteralPath 'Start-LineaForge.cmd' -Destination $portableDirectory -Force
Copy-Item -LiteralPath 'Run-LineaForge.cmd' -Destination $portableDirectory -Force
Copy-Item -LiteralPath 'docs\web_product.md' -Destination (Join-Path $portableDirectory 'SETUP.md') -Force
Copy-Item -LiteralPath 'docs\web_cad_setup.md' -Destination $portableDirectory -Force
Set-Content -LiteralPath 'dist\web-current.txt' -Value $portableDirectory.Substring($projectRoot.Length + 1) -Encoding ascii
& $Python scripts/package_web.py
if ($LASTEXITCODE -ne 0) { throw 'Web package failed' }
