param([string]$Destination)
$ErrorActionPreference = 'Stop'
$sourceFolder = $PSScriptRoot
if (!$Destination) {
    $pointerFile = Join-Path $sourceFolder 'install-location.json'
    if (Test-Path -LiteralPath $pointerFile) {
        $Destination = (Get-Content -Raw -LiteralPath $pointerFile | ConvertFrom-Json).root
    } else {
        $Destination = Read-Host 'Existing installation folder [D:\SheruStudio]'
        if ([string]::IsNullOrWhiteSpace($Destination)) { $Destination='D:\SheruStudio' }
    }
}
$installRoot = [IO.Path]::GetFullPath($Destination)
$python = Join-Path $installRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath (Join-Path $installRoot 'install-location.json'))) {
    throw 'An installed Sheru Studio environment was not found there. Run Setup.cmd for first installation.'
}
Write-Host "Updating Sheru Studio in $installRoot" -ForegroundColor Cyan
# Stop only the installed Studio backend belonging to this environment.
# Workers, Ollama, unrelated Python processes and the browser are separate.
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $python -and $_.CommandLine -match '(?i)(^|[\s"\\/])studio\.py(["\s]|$)'
})
foreach ($process in $running) {
    Write-Host "Restarting Studio process $($process.ProcessId) ..."
    Stop-Process -Id $process.ProcessId -ErrorAction Stop
}
if ($running.Count) { Start-Sleep -Seconds 2 }
if ($installRoot.TrimEnd('\') -ine $sourceFolder.TrimEnd('\')) {
    Get-ChildItem -LiteralPath $sourceFolder -File | Where-Object {
        $_.Extension -in @('.py','.ps1','.cmd','.txt') -or $_.Name -eq 'episode01.json'
    } | ForEach-Object {
        $target = Join-Path $installRoot $_.Name
        if ($_.Name -ne 'episode01.json' -or !(Test-Path -LiteralPath $target)) {
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
    foreach ($folder in @('web','assets')) {
        $target = Join-Path $installRoot $folder
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        Get-ChildItem -LiteralPath (Join-Path $sourceFolder $folder) | Copy-Item -Destination $target -Recurse -Force
    }
}
@{root=$installRoot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $sourceFolder 'install-location.json') -Encoding UTF8
Write-Host 'Application update complete. Opening Studio ...' -ForegroundColor Green
& (Join-Path $installRoot 'launch.ps1') -Action studio
