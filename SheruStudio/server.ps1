$port = 11435
if ($env:OLLAMA_HOST) {
    $modelUri = $env:OLLAMA_HOST
    if ($modelUri -notmatch '^https?://') { $modelUri='http://'+$modelUri }
    $port=([uri]$modelUri).Port
}
try {
    Invoke-RestMethod "http://127.0.0.1:$port/api/tags" -TimeoutSec 2 | Out-Null
} catch {
    $serverProcess = Start-Process -FilePath (Join-Path $env:SHERU_ROOT 'tools\ollama\ollama.exe') -ArgumentList 'serve' -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $env:SHERU_ROOT 'logs\ollama-out.log') -RedirectStandardError (Join-Path $env:SHERU_ROOT 'logs\ollama-error.log')
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        if ($serverProcess.HasExited) { throw 'Ollama stopped. Check logs\ollama-error.log.' }
        try {
            Invoke-RestMethod "http://127.0.0.1:$port/api/tags" -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        } catch {}
    }
    if (!$ready) { throw 'Ollama did not start. Check logs\ollama-error.log.' }
}
