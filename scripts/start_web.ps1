param([int]$Port = 8765, [string]$HostName = '127.0.0.1')
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
Set-Location -LiteralPath $projectRoot
$localRuntime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython) {
    $pythonPath = $venvPython
    $env:PYTHONPATH = $projectRoot
} elseif ((Test-Path -LiteralPath $localRuntime) -and (Test-Path -LiteralPath '.webruntime\fastapi')) {
    $pythonPath = $localRuntime
    $env:PYTHONPATH = @($projectRoot,(Join-Path $projectRoot '.webruntime-secure'),(Join-Path $projectRoot '.webruntime'),(Join-Path $projectRoot '.runtime'),(Join-Path $projectRoot '.runtime\win32'),(Join-Path $projectRoot '.runtime\win32\lib'),(Join-Path $projectRoot '.runtime\pywin32_system32')) -join ';'
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Use the portable Windows ZIP, or install Python 3.12/3.13 and Node.js 24 to run from source.' }
    & $pythonCommand.Source -c 'import sys; assert (3,12) <= sys.version_info[:2] < (3,14), "Python 3.12 or 3.13 is required"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or 3.13 is required. The portable ZIP includes Python.' }
    & $pythonCommand.Source -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment' }
    $pythonPath = $venvPython
}
& $pythonPath scripts/check_web_runtime.py
if ($LASTEXITCODE -ne 0) {
    if ($pythonPath -eq $localRuntime) { throw 'The prepared runtime is incomplete. Use the portable Windows ZIP or a clean Python virtual environment.' }
    & $pythonPath -m pip install -r requirements-web.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; click again to retry.' }
    & $pythonPath scripts/check_web_runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency checks failed' }
}
if (-not (Test-Path -LiteralPath 'web\dist\index.html')) {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npmCommand) { throw 'Node.js 24 is needed for the first source build. The portable ZIP includes the compiled frontend.' }
    Push-Location -LiteralPath (Join-Path $projectRoot 'web')
    try {
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed' }
        & $npmCommand.Source run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
& $pythonPath -m lineaforge.web_launcher --open --host $HostName --port $Port
exit $LASTEXITCODE
