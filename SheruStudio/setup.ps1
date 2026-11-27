param([string]$Destination,[switch]$RenderPilot)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'Continue'
$sourceFolder = $PSScriptRoot
if (!$Destination) {
    $Destination = Read-Host 'Install folder [D:\SheruStudio] - press Enter for D or type another full folder'
    if ([string]::IsNullOrWhiteSpace($Destination)) { $Destination = 'D:\SheruStudio' }
}
if ($Destination -notmatch '^[A-Za-z]:\\[^<>:"|?*]+$') { throw 'Choose a full local folder such as D:\SheruStudio.' }
$installRoot = [IO.Path]::GetFullPath($Destination)
$driveRoot = [IO.Path]::GetPathRoot($installRoot)
if (!(Test-Path -LiteralPath $driveRoot)) { throw "Drive $driveRoot is unavailable. Run Setup again and choose an available drive." }
if ($installRoot.TrimEnd('\') -eq $driveRoot.TrimEnd('\')) { throw 'Choose an application folder inside the drive.' }
if ($installRoot.TrimEnd('\') -ine $sourceFolder.TrimEnd('\') -and (Test-Path -LiteralPath $installRoot)) {
    $existing = @(Get-ChildItem -LiteralPath $installRoot -Force)
    if ($existing.Count -gt 0 -and !(Test-Path -LiteralPath (Join-Path $installRoot 'install-location.json'))) {
        throw 'That folder already contains files. Choose an empty folder or the current extracted SheruStudio folder.'
    }
}
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Write-Host "Installing Sheru Studio in $installRoot"
Write-Host 'Windows may retain installer records and account credentials on the system drive.'
if ($installRoot.TrimEnd('\') -ine $sourceFolder.TrimEnd('\')) {
    Get-ChildItem -LiteralPath $sourceFolder -File | Where-Object {
        $_.Extension -in @('.py','.ps1','.cmd','.txt') -or $_.Name -eq 'episode01.json'
    } | ForEach-Object {
        $targetFile = Join-Path $installRoot $_.Name
        if ($_.Name -ne 'episode01.json' -or !(Test-Path -LiteralPath $targetFile)) {
            Copy-Item -LiteralPath $_.FullName -Destination $targetFile -Force
        }
    }
}
foreach ($assetFolder in @('web','assets')) {
    $sourceAssets = Join-Path $sourceFolder $assetFolder
    $targetAssets = Join-Path $installRoot $assetFolder
    if ($sourceFolder.TrimEnd('\') -ine $installRoot.TrimEnd('\')) {
        New-Item -ItemType Directory -Path $targetAssets -Force | Out-Null
        Get-ChildItem -LiteralPath $sourceAssets | Copy-Item -Destination $targetAssets -Recurse -Force
    }
}
$pointer = @{root=$installRoot} | ConvertTo-Json
$pointer | Set-Content -LiteralPath (Join-Path $sourceFolder 'install-location.json') -Encoding UTF8
$pointer | Set-Content -LiteralPath (Join-Path $installRoot 'install-location.json') -Encoding UTF8
if ($RenderPilot -and !(Test-Path -LiteralPath (Join-Path $installRoot 'studio-port.txt'))) {
    # Fresh install uses a free local port so an older Studio can remain open.
    $selectedPort = $null
    foreach ($candidatePort in 8766..8799) {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$candidatePort)
        try { $listener.Start(); $selectedPort=$candidatePort; break } catch {} finally { $listener.Stop() }
    }
    if (!$selectedPort) { throw 'No free local Studio port was found in 8766-8799. Close the older Studio window and retry.' }
    [string]$selectedPort | Set-Content -LiteralPath (Join-Path $installRoot 'studio-port.txt') -Encoding ASCII
}
if ($RenderPilot -and !(Test-Path -LiteralPath (Join-Path $installRoot 'model-port.txt'))) {
    $selectedModelPort = $null
    foreach ($candidatePort in 11436..11469) {
        $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,$candidatePort)
        try { $listener.Start(); $selectedModelPort=$candidatePort; break } catch {} finally { $listener.Stop() }
    }
    if (!$selectedModelPort) { throw 'No free local model-service port was found in 11436-11469.' }
    [string]$selectedModelPort | Set-Content -LiteralPath (Join-Path $installRoot 'model-port.txt') -Encoding ASCII
}
. (Join-Path $installRoot 'runtime.ps1') -InstallRoot $installRoot
$downloads = Join-Path $installRoot 'downloads'
New-Item -ItemType Directory -Path $downloads -Force | Out-Null
$detailStatus = Join-Path $installRoot 'logs\setup-detail.json'
$stageStatus = Join-Path $installRoot 'logs\setup-status.json'
$setupStarted = Get-Date
trap {
    $failure = $_.Exception.Message
    try {
        if ($stageStatus) {
            @{step=$script:currentStage;total=8;label=$script:currentLabel;state='failed';error=$failure;updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $stageStatus -Encoding UTF8
        }
        Write-Progress -Id 1 -Activity 'Sheru Studio setup' -Completed
    } catch {}
    Write-Host ""
    Write-Host ("SETUP STOPPED: "+$failure) -ForegroundColor Red
    Write-Host 'Completed tools are retained. Rerun Setup.cmd to retry.'
    exit 1
}

function Set-Stage([int]$Index,[string]$Label) {
    $script:currentStage = $Index
    $script:currentLabel = $Label
    Write-Host ""
    Write-Host "[$Index/8] $Label" -ForegroundColor Cyan
    Write-Progress -Id 1 -Activity 'Sheru Studio setup' -Status "Step $Index of 8 - $Label" -PercentComplete ([int](($Index-1)*100/8))
    @{step=$Index;total=8;label=$Label;state='running';updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $stageStatus -Encoding UTF8
}
function Check-Exit([string]$Label) {
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit $LASTEXITCODE). Completed tools are kept. Rerun Setup to retry." }
}
function Download-File([string]$Url,[string]$File,[string]$ExpectedHash='',[switch]$Refresh) {
    if (Test-Path -LiteralPath $script:python) {
        $arguments = @((Join-Path $installRoot 'setup_progress.py'),'download',$Url,$File,'--status',$detailStatus)
        if ($ExpectedHash) { $arguments += @('--sha256',$ExpectedHash) }
        if ($Refresh) { $arguments += '--refresh' }
        & $script:python @arguments
        Check-Exit 'Download'
        return
    }
    # Bootstrap the Python runtime with Windows' built-in curl progress meter.
    $pending = $File+'.partial'
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    Write-Host "Downloading $([IO.Path]::GetFileName($File)) - progress below"
    if ($curl) {
        & $curl.Source --fail --location --connect-timeout 30 --speed-time 60 --speed-limit 1 --retry 3 --retry-delay 3 --output $pending $Url
        Check-Exit 'Python bootstrap download'
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $pending -TimeoutSec 90
    }
    Move-Item -LiteralPath $pending -Destination $File -Force
}
function Expand-Visible([string]$Archive,[string]$DestinationFolder) {
    & $script:python (Join-Path $installRoot 'setup_progress.py') extract $Archive $DestinationFolder --status $detailStatus
    Check-Exit 'Archive extraction'
}
function Wait-Visible([Diagnostics.Process]$Process,[string]$Label) {
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while (!$Process.WaitForExit(1000)) {
        Write-Progress -Id 2 -ParentId 1 -Activity $Label -Status ("Installer process "+$Process.Id+" is open - elapsed "+[int]$clock.Elapsed.TotalSeconds+" seconds") -PercentComplete -1
        if ([int]$clock.Elapsed.TotalSeconds % 10 -eq 0) {
            Write-Host ("Still running: "+$Label+" | process "+$Process.Id+" | elapsed "+[int]$clock.Elapsed.TotalSeconds+"s")
        }
    }
    Write-Progress -Id 2 -Activity $Label -Completed
}
Set-Stage 1 'Workspace and D-drive paths ready'
$pythonDir = Join-Path $installRoot 'tools\python'
$python = Join-Path $pythonDir 'python.exe'
Set-Stage 2 'Installing/checking Python runtime'
$priorPython = Join-Path $driveRoot 'SheruStudio\tools\python\python.exe'
if ($RenderPilot -and (Test-Path -LiteralPath $priorPython) -and
    (!(Test-Path -LiteralPath $python) -or (Test-Path -LiteralPath (Join-Path $pythonDir '.sheru-installing'))) -and
    [IO.Path]::GetFullPath($priorPython) -ine [IO.Path]::GetFullPath($python)) {
    # Copy the previous D-drive runtime; do not move it or alter its installation.
    # This avoids Windows installer maintenance mode for the already installed version.
    Write-Host 'Copying the existing D-drive Python runtime into the fresh installation ...' -ForegroundColor Cyan
    & $priorPython (Join-Path $installRoot 'setup_progress.py') copy (Split-Path -Parent $priorPython) $pythonDir --status $detailStatus
    Check-Exit 'Fresh Python runtime copy'
}
if (!(Test-Path -LiteralPath $python)) {
    $installer = Join-Path $downloads 'python-3.13.16-amd64.exe'
    Download-File 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe' $installer
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw 'Python installer signature verification failed.'
    }
    $arguments = @('/quiet','InstallAllUsers=0',('TargetDir="'+$pythonDir+'"'),'Include_launcher=0','InstallLauncherAllUsers=0','PrependPath=0','AssociateFiles=0','Shortcuts=0','Include_test=0','Include_doc=0','Include_tcltk=1','Include_pip=1')
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -PassThru -WindowStyle Hidden
    Wait-Visible $process 'Installing Python in the selected folder'
    if ($process.ExitCode -notin @(0,3010) -or !(Test-Path -LiteralPath $python)) {
        throw 'Python did not install in the selected folder. Check whether this Python version is already installed elsewhere.'
    }
}
Set-Stage 3 'Downloading/checking and extracting FFmpeg'
$ffmpegExe = Join-Path $installRoot 'tools\ffmpeg\bin\ffmpeg.exe'
if (!(Test-Path -LiteralPath $ffmpegExe) -or (Test-Path -LiteralPath (Join-Path $installRoot 'tools\ffmpeg\.sheru-installing'))) {
    $archive = Join-Path $downloads 'ffmpeg.zip'
    $hashFile = Join-Path $downloads 'ffmpeg.sha256'
    Download-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip.sha256' $hashFile -Refresh
    $hashText = Get-Content -Raw -LiteralPath $hashFile
    $expectedHash = [regex]::Match([string]$hashText,'(?i)\b[a-f0-9]{64}\b').Value
    if (!$expectedHash) { throw 'FFmpeg publisher returned an unreadable checksum.' }
    Download-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $archive $expectedHash
    $unpacked = Join-Path $installRoot ('temp\ffmpeg-'+[guid]::NewGuid().ToString('N'))
    Expand-Visible $archive $unpacked
    $binary = @(Get-ChildItem -LiteralPath $unpacked -Filter 'ffmpeg.exe' -Recurse)
    if ($binary.Count -ne 1) { throw 'FFmpeg archive layout was unexpected.' }
    $buildFolder = $binary[0].Directory.Parent.FullName
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'tools\ffmpeg') -Force | Out-Null
    & $python (Join-Path $installRoot 'setup_progress.py') copy $buildFolder (Join-Path $installRoot 'tools\ffmpeg') --status $detailStatus
    Check-Exit 'FFmpeg tool installation'
}
Set-Stage 4 'Downloading/checking and extracting Ollama'
$ollama = Join-Path $installRoot 'tools\ollama\ollama.exe'
if (!(Test-Path -LiteralPath $ollama) -or (Test-Path -LiteralPath (Join-Path $installRoot 'tools\ollama\.sheru-installing'))) {
    $archive = Join-Path $downloads 'ollama-windows-amd64.zip'
    $hashFile = Join-Path $downloads 'ollama-windows-amd64.sha256'
    # The release publishes sha256sum.txt; verify the archive instead of trusting the download.
    Download-File 'https://github.com/ollama/ollama/releases/latest/download/sha256sum.txt' $hashFile -Refresh
    $ollamaHash = [regex]::Match([string](Get-Content -Raw -LiteralPath $hashFile),'(?im)^([a-f0-9]{64})\s+\*?.*ollama-windows-amd64\.zip\s*$').Groups[1].Value
    if (!$ollamaHash) { throw 'Ollama publisher returned an unreadable checksum.' }
    Download-File 'https://github.com/ollama/ollama/releases/latest/download/ollama-windows-amd64.zip' $archive $ollamaHash
    $ollamaStage = Join-Path $installRoot ('temp\ollama-'+[guid]::NewGuid().ToString('N'))
    Expand-Visible $archive $ollamaStage
    New-Item -ItemType Directory -Path (Join-Path $installRoot 'tools\ollama') -Force | Out-Null
    & $python (Join-Path $installRoot 'setup_progress.py') copy $ollamaStage (Join-Path $installRoot 'tools\ollama') --status $detailStatus
    Check-Exit 'Ollama tool installation'
    if (!(Test-Path -LiteralPath $ollama) -or (Test-Path -LiteralPath (Join-Path $installRoot 'tools\ollama\.sheru-installing'))) { throw 'Ollama extraction failed.' }
}
Set-Stage 5 'Creating/checking the application environment'
$venvPython = Join-Path $installRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $venvPython)) {
    & $python -m venv (Join-Path $installRoot '.venv')
    if ($LASTEXITCODE) { throw 'Python environment creation failed.' }
}
Set-Stage 6 'Installing application packages - live output and elapsed time below'
& $python (Join-Path $installRoot 'setup_progress.py') run --label 'Installing application packages' --status $detailStatus -- $venvPython -m pip install --progress-bar off --timeout 30 --retries 3 -r requirements.txt
Check-Exit 'Application package installation'
Set-Stage 7 'Preparing local services and pilot assets'
. (Join-Path $installRoot 'server.ps1')
if ($RenderPilot) {
    Write-Host 'The pilot has bundled scene frames and complete Tamil recordings.' -ForegroundColor Green
    Write-Host 'The first animated render uses cloud video generation. Provider quota and account access still apply.'
    Write-Host 'Writing and cloud-review models install automatically when their tools are first used.'
} else {
    & $python (Join-Path $installRoot 'setup_progress.py') pull --host $env:OLLAMA_HOST --model qwen2.5:3b --status $detailStatus
    Check-Exit 'Script model download'
    & $python (Join-Path $installRoot 'setup_progress.py') pull --host $env:OLLAMA_HOST --model qwen2.5vl:3b --status $detailStatus
    Check-Exit 'Visual reviewer download'
}
Set-Stage 8 'Checking tools and opening Studio'
& $venvPython production.py doctor
if ($LASTEXITCODE) { throw 'Health check failed.' }
Write-Progress -Id 1 -Activity 'Sheru Studio setup' -Completed
@{step=8;total=8;label='Setup complete';state='complete';updated=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $stageStatus -Encoding UTF8
Write-Host ("Setup complete in $installRoot | elapsed "+[int]((Get-Date)-$setupStarted).TotalSeconds+" seconds") -ForegroundColor Green
Write-Host 'Studio opens now. Your scripts, account buttons, shot inspector and review room are inside.'
if ($RenderPilot) {
    Write-Host 'Starting the animated episode with automatic review, voice repair and visual regeneration.' -ForegroundColor Cyan
    & $venvPython studio.py --animate-pilot
} else {
    & $venvPython studio.py
}
if ($LASTEXITCODE) { throw 'Studio exited with an error.' }
