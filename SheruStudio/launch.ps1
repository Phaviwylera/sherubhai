param([ValidateSet('accounts','pilot','console','worker','studio')][string]$Action='accounts')
$ErrorActionPreference = 'Stop'
$pointerFile = Join-Path $PSScriptRoot 'install-location.json'
if (!(Test-Path -LiteralPath $pointerFile)) { throw 'Run Setup.cmd first.' }
$installRoot = (Get-Content -Raw -LiteralPath $pointerFile | ConvertFrom-Json).root
. (Join-Path $installRoot 'runtime.ps1') -InstallRoot $installRoot
$python = Join-Path $installRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python)) { throw 'Setup is incomplete. Run Setup.cmd again.' }
switch ($Action) {
    'studio' {
        . (Join-Path $installRoot 'server.ps1')
        & $python studio.py
    }
    'accounts' { & $python accounts.py }
    'pilot' {
        . (Join-Path $installRoot 'server.ps1')
        & $python studio.py
    }
    'worker' {
        . (Join-Path $installRoot 'server.ps1')
        & $python releases.py watch
    }
    'console' {
        . (Join-Path $installRoot 'server.ps1')
        $env:Path = (Join-Path $installRoot '.venv\Scripts')+';'+$env:Path
        Write-Host 'Sheru Studio console. Python, models, cache and temporary files use your chosen folder.'
        & powershell -NoProfile -NoExit
    }
}
if ($LASTEXITCODE) { throw "Command exited with code $LASTEXITCODE." }
