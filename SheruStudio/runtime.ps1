param([Parameter(Mandatory=$true)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot)
$env:SHERU_ROOT = $InstallRoot
$portFile = Join-Path $InstallRoot 'studio-port.txt'
if (Test-Path -LiteralPath $portFile) {
    $portValue = [int](Get-Content -Raw -LiteralPath $portFile).Trim()
    if ($portValue -lt 1024 -or $portValue -gt 65535) { throw 'Invalid local Studio port in studio-port.txt.' }
    $env:SHERU_PORT = [string]$portValue
}
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
$env:OLLAMA_HOST = '127.0.0.1:11435'
$modelPortFile = Join-Path $InstallRoot 'model-port.txt'
if (Test-Path -LiteralPath $modelPortFile) {
    $modelPort = [int](Get-Content -Raw -LiteralPath $modelPortFile).Trim()
    if ($modelPort -lt 1024 -or $modelPort -gt 65535) { throw 'Invalid local model-service port.' }
    $env:OLLAMA_HOST = '127.0.0.1:'+[string]$modelPort
}
$env:OLLAMA_MODELS = Join-Path $InstallRoot 'models'
$env:OLLAMA_TMPDIR = Join-Path $InstallRoot 'temp'
$env:TEMP = Join-Path $InstallRoot 'temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $InstallRoot 'cache\pip'
$env:HF_HOME = Join-Path $InstallRoot 'cache\huggingface'
$env:GRADIO_TEMP_DIR = Join-Path $InstallRoot 'temp\gradio'
$env:XDG_CACHE_HOME = Join-Path $InstallRoot 'cache'
$env:PYTHONPYCACHEPREFIX = Join-Path $InstallRoot 'cache\pycache'
$env:Path = (Join-Path $InstallRoot 'tools\ffmpeg\bin')+';'+(Join-Path $InstallRoot 'tools\ollama')+';'+$env:Path
@('temp','temp\gradio','cache\pip','cache\huggingface','cache\pycache','models','logs') | ForEach-Object {
    New-Item -ItemType Directory -Path (Join-Path $InstallRoot $_) -Force | Out-Null
}
Set-Location -LiteralPath $InstallRoot
