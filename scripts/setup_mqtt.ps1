<#
    setup_mqtt.ps1  -  install + run a local Mosquitto MQTT broker for the hub

    Installs Eclipse Mosquitto (via winget) if it isn't already on this box,
    then starts it with the repo's local-loopback config
    (scripts/mosquitto.conf). Idempotent: if mosquitto is already installed,
    installation is skipped.

    This is a standalone process, independent of hub/server.py's lifecycle -
    start it once, then start/restart the hub freely without losing the
    broker or any devices subscribed to it.

    Usage (from an elevated or normal PowerShell prompt, inside the checkout):
        powershell -ExecutionPolicy Bypass -File .\scripts\setup_mqtt.ps1

      -NoRun            stop after installing; don't start the broker
#>

param(
    [switch]$NoRun
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$RepoDir = Split-Path $PSScriptRoot -Parent
$ConfPath = Join-Path $PSScriptRoot 'mosquitto.conf'

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# --- 1. Mosquitto CLI --------------------------------------------------------
Write-Step "Ensuring Mosquitto is installed"
if (Get-Command mosquitto -ErrorAction SilentlyContinue) {
    Write-Ok "mosquitto already present: $(mosquitto -h 2>&1 | Select-Object -First 1)"
} else {
    Write-Host "    Installing Mosquitto via winget..."
    winget install --id EclipseFoundation.Mosquitto -e --source winget `
        --accept-package-agreements --accept-source-agreements
    # Refresh PATH for the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    if (Get-Command mosquitto -ErrorAction SilentlyContinue) {
        Write-Ok "mosquitto installed"
    } else {
        throw ("mosquitto install did not surface on PATH. Open a new shell and re-run, " +
               "or install manually from https://mosquitto.org/download/.")
    }
}

# --- 2. paho-mqtt (hub's client library) ------------------------------------
Write-Step "Note: hub/requirements.txt includes paho-mqtt for the hub side"
Write-Host "    Run scripts\setup_geniex.ps1 (or 'pip install -r hub\requirements.txt')" -ForegroundColor Yellow
Write-Host "    to install it into the hub's venv, if you haven't already." -ForegroundColor Yellow

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host " Mosquitto ready. Config: $ConfPath" -ForegroundColor Green
Write-Host " Hub connects to it at 127.0.0.1:1883 by default (QONCLAVE_MQTT_HOST/PORT)." -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green

# --- 3. Run the broker --------------------------------------------------------
if ($NoRun) {
    Write-Step "NoRun set - skipping broker start"
    Write-Host "    Start it later with:  mosquitto -c `"$ConfPath`" -v"
} else {
    Write-Step "Starting Mosquitto (Ctrl+C to stop)"
    Set-Location $RepoDir
    & mosquitto -c $ConfPath -v
}
