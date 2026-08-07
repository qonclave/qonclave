<#
    setup_mqtt.ps1  -  install + run a local amqtt MQTT broker for the hub

    Installs the `amqtt` Python package (via pip) if it isn't already
    present, then runs hub/run_mqtt_broker.py, which starts an anonymous
    amqtt broker on 127.0.0.1:1883.

    This is a standalone process, independent of hub/server.py's lifecycle -
    start it once, then start/restart the hub freely without losing the
    broker or any devices subscribed to it.

    Usage (from an elevated or normal PowerShell prompt, inside the checkout):
        powershell -ExecutionPolicy Bypass -File .\examples\hub\setup_mqtt.ps1

      -NoRun            stop after installing; don't start the broker
      -HostAddress      address for the broker to listen on (default: 0.0.0.0)
      -Port             TCP port for the broker to listen on (default: 1883)
#>

param(
    [switch]$NoRun,
    [string]$HostAddress = '0.0.0.0',
    [int]$Port = 1883
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

$RepoDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$BrokerScript = Join-Path $PSScriptRoot 'run_mqtt_broker.py'

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

function Get-Python {
    # Resolve a usable Python 3 interpreter, ignoring Windows Store app execution aliases (\WindowsApps\).
    function Test-UsablePython($exe) {
        if (-not $exe -or ($exe -match '\\WindowsApps\\')) { return $false }
        if (-not (Test-Path $exe)) { return $false }
        $ver = (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
        return ($LASTEXITCODE -eq 0 -and $ver)
    }

    # 1. py launcher (if present)
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p -and (Test-UsablePython $p.Trim())) {
            return $p.Trim()
        }
    }

    # 2. Well-known Python install paths
    $roots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python*",
        "${env:ProgramFiles(Arm)}\Python*",
        "C:\Python*"
    )
    foreach ($root in $roots) {
        if ($root -and (Test-Path $root)) {
            foreach ($f in (Get-ChildItem -Path $root -Filter python.exe -Depth 1 -ErrorAction SilentlyContinue |
                            Select-Object -ExpandProperty FullName)) {
                if (Test-UsablePython $f) { return $f }
            }
        }
    }

    # 3. Check all python / python3 executables on PATH, excluding WindowsApps
    foreach ($name in @('python.exe', 'python3.exe')) {
        foreach ($c in (Get-Command $name -All -ErrorAction SilentlyContinue)) {
            if ($c.Source -and (Test-UsablePython $c.Source)) { return $c.Source }
        }
    }

    return $null
}

# --- 1. Python ---------------------------------------------------------------
Write-Step "Locating Python"
$PythonExe = Get-Python
if (-not $PythonExe) {
    throw "No Python 3 interpreter found on PATH. Install Python 3.10+ and re-run."
}
Write-Ok "using Python: $PythonExe"

# --- 2. amqtt (the broker library run_mqtt_broker.py depends on) ------------
Write-Step "Ensuring amqtt is installed"
& $PythonExe -m pip install amqtt
if ($LASTEXITCODE -ne 0) {
    throw "pip install amqtt failed (exit $LASTEXITCODE)."
}
Write-Ok "amqtt installed"

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host " amqtt ready. Broker script: $BrokerScript" -ForegroundColor Green
Write-Host " Hub connects to it at 127.0.0.1:1883 by default (QONCLAVE_MQTT_HOST/PORT)." -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green

# --- 3. Run the broker --------------------------------------------------------
if ($NoRun) {
    Write-Step "NoRun set - skipping broker start"
    Write-Host "    Start it later with:  & `"$PythonExe`" `"$BrokerScript`" --host $HostAddress --port $Port"
} else {
    Write-Step "Starting amqtt broker (Ctrl+C to stop)"
    Set-Location $RepoDir
    & $PythonExe $BrokerScript --host $HostAddress --port $Port
}
