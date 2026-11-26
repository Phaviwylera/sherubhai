param([string]$Destination)
$ErrorActionPreference = 'Stop'
Write-Host 'SHERU STUDIO - FRESH INSTALL AND COMPLETE PILOT' -ForegroundColor Cyan
Write-Host 'This starts the 30-second ANIMATED episode pipeline with automatic review and repair.'
Write-Host 'Scene references and exact Tamil recordings are included. Cloud animation requires provider access and available quota.'
if (!$Destination) {
    $Destination=Read-Host 'Fresh install folder [D:\SheruStudio-Fresh] - Enter for default, or type a full folder'
    if ([string]::IsNullOrWhiteSpace($Destination)) { $Destination='D:\SheruStudio-Fresh' }
}
& (Join-Path $PSScriptRoot 'setup.ps1') -Destination $Destination -RenderPilot
