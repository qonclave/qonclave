<#
    setup_ngrok.ps1  -  expose the Qonclave hub outside the local network via ngrok

    Installs ngrok (via winget, falling back to a direct zip download), updates
    it to a version new enough for your ngrok account, configures your
    authtoken, starts the hub server if it isn't already running, opens an
    ngrok HTTP tunnel to it, and prints the public URL. Idempotent: re-running
    it reuses an already-installed ngrok, an already-running hub server, and
    an already-open tunnel instead of starting duplicates.

    The hub has NO authentication on any route - anyone with the ngrok URL can
    hit /edge/event, view camera frames, and use the dashboard. Treat the
    printed URL as a secret.

    ngrok's free tier shows a "you're about to visit..." interstitial page to
    real browsers (not to API/device clients like the camera's POST
    /edge/event) - click "Visit Site" once per browser. It can only be
    removed entirely with a paid ngrok plan.

    Usage (from inside this checkout):
        powershell -ExecutionPolicy Bypass -File .\hub\setup_ngrok.ps1
        powershell -ExecutionPolicy Bypass -File .\hub\setup_ngrok.ps1 -AuthToken <token>

      -AuthToken              ngrok authtoken (from https://dashboard.ngrok.com/get-started/your-authtoken).
                              Prompted interactively if omitted and not already configured.
      -Port                   local hub port to tunnel (default 8000, matches hub/server.py's default).
      -SkipServerStart        don't try to start hub/server.py; assume it's already running on -Port.
      -AllowDefenderExclusion ngrok's self-updater occasionally gets quarantined by Windows Defender as
                              a false positive (equinox.io-signed binaries trip some signature sets). If
                              set, and that happens, this script adds a Defender path exclusion for
                              ngrok's install folder and retries. Requires an elevated PowerShell.
                              Off by default since it's a Defender config change.
#>

param(
    [string]$AuthToken = '',
    [int]$Port = 8000,
    [switch]$SkipServerStart,
    [switch]$AllowDefenderExclusion
)

$ErrorActionPreference = 'Stop'
# See setup_hub.ps1 for why this is off: we probe commands that may legitimately
# fail (e.g. `ngrok update` when already current) and check $LASTEXITCODE ourselves.
$PSNativeCommandUseErrorActionPreference = $false

# This script lives at <repo>/hub/setup_ngrok.ps1.
$HubDir  = $PSScriptRoot
$RepoDir = Split-Path $HubDir -Parent
$NgrokFallbackDir = Join-Path $HubDir 'ngrok-bin'
$NgrokFallbackZipUrl = 'https://bin.equinox.io/c/4VmDzA7iaHb/ngrok-v3-stable-windows-amd64.zip'

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# --- helpers -----------------------------------------------------------------

function Get-NgrokExe {
    # Resolve ngrok.exe by absolute path, checking (in order): PATH, the winget
    # install location, then our own fallback download folder. Never assumes
    # PATH is fresh - winget updates PATH for new shells, not this session.
    $cmd = Get-Command ngrok.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $wingetPath = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ngrok.exe'
    if (Test-Path $wingetPath) { return $wingetPath }

    $fallbackPath = Join-Path $NgrokFallbackDir 'ngrok.exe'
    if (Test-Path $fallbackPath) { return $fallbackPath }

    return $null
}

function Install-NgrokViaWinget {
    Write-Host "    Installing ngrok via winget..."
    winget install --id Ngrok.Ngrok -e --source winget --accept-package-agreements --accept-source-agreements
    return ($LASTEXITCODE -eq 0)
}

function Install-NgrokViaDirectDownload {
    Write-Host "    Downloading ngrok directly: $NgrokFallbackZipUrl"
    New-Item -ItemType Directory -Force -Path $NgrokFallbackDir | Out-Null
    $zipPath = Join-Path $env:TEMP 'ngrok-stable-windows-amd64.zip'
    Invoke-WebRequest -Uri $NgrokFallbackZipUrl -OutFile $zipPath
    Expand-Archive -Path $zipPath -DestinationPath $NgrokFallbackDir -Force
    Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
}

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- 1. Install ngrok if missing ----------------------------------------------
Write-Step "Ensuring ngrok is installed"
$ngrokExe = Get-NgrokExe
if ($ngrokExe) {
    Write-Ok "ngrok already present: $ngrokExe"
} else {
    $wingetOk = $true
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            $wingetOk = Install-NgrokViaWinget
        } catch {
            Write-Warn "winget failed to run ($($_.Exception.Message)); falling back to a direct download."
            $wingetOk = $false
        }
    } else {
        $wingetOk = $false
    }

    if (-not $wingetOk) {
        Install-NgrokViaDirectDownload
    }

    $ngrokExe = Get-NgrokExe
    if (-not $ngrokExe) {
        throw "ngrok install did not produce a usable ngrok.exe. Install it manually from https://ngrok.com/download and re-run."
    }
    Write-Ok "ngrok installed: $ngrokExe"
}

# --- 2. Update to a version new enough for the account ------------------------
# ngrok accounts commonly require a newer agent than winget's packaged 3.3.1
# (ERR_NGROK_121). `ngrok update` needs no authtoken, so this runs before auth
# is configured. Windows Defender has been seen to quarantine the freshly
# updated binary as a false positive - detect that and, only if explicitly
# allowed, add a targeted exclusion and retry once.
Write-Step "Updating ngrok to the latest version"
& $ngrokExe update
$ngrokExe = Get-NgrokExe
if (-not $ngrokExe) {
    Write-Warn "ngrok.exe vanished after 'ngrok update' - Windows Defender likely quarantined it as a false positive."
    if ($AllowDefenderExclusion) {
        if (-not (Test-IsElevated)) {
            throw "Adding a Defender exclusion needs an elevated PowerShell. Re-run this script as Administrator with -AllowDefenderExclusion."
        }
        $exclusionDir = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'
        Write-Host "    Adding Defender exclusion for $exclusionDir ..."
        Add-MpPreference -ExclusionPath $exclusionDir
        Write-Ok "exclusion added"

        Write-Host "    Reinstalling and re-updating ngrok..."
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id Ngrok.Ngrok -e --source winget --accept-package-agreements --accept-source-agreements --force | Out-Null
        } else {
            Install-NgrokViaDirectDownload
        }
        $ngrokExe = Get-NgrokExe
        if (-not $ngrokExe) { throw "ngrok still missing after reinstall. Install manually from https://ngrok.com/download." }
        & $ngrokExe update
        $ngrokExe = Get-NgrokExe
        if (-not $ngrokExe) { throw "ngrok was quarantined again even with the exclusion in place. Investigate manually via Get-MpThreatDetection." }
        Write-Ok "ngrok updated: $(& $ngrokExe version)"
    } else {
        throw ("ngrok.exe was removed by Windows Defender after updating (known false positive). Re-run with " +
               "-AllowDefenderExclusion from an elevated PowerShell to add a scoped exclusion and retry, or " +
               "restore/allow it yourself via Windows Security > Virus & threat protection > Protection history.")
    }
} else {
    Write-Ok "ngrok is up to date: $(& $ngrokExe version)"
}

# --- 3. Configure the authtoken ------------------------------------------------
Write-Step "Configuring ngrok authtoken"
if (-not $AuthToken) {
    $secure = Read-Host "Enter your ngrok authtoken (https://dashboard.ngrok.com/get-started/your-authtoken)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        $AuthToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
if (-not $AuthToken) {
    throw "No authtoken provided. Pass -AuthToken <token> or enter one when prompted."
}
& $ngrokExe config add-authtoken $AuthToken
if ($LASTEXITCODE -ne 0) { throw "ngrok rejected the authtoken (exit $LASTEXITCODE)." }
Write-Ok "authtoken configured"

# --- 4. Start the hub server, if it isn't already running ---------------------
Write-Step "Checking hub server on port $Port"
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Ok "hub server already listening on port $Port"
} elseif ($SkipServerStart) {
    Write-Warn "-SkipServerStart set and nothing is listening on port $Port - the tunnel will point at a dead port until you start it."
} else {
    $venvPython = Join-Path $HubDir 'geniex-env\Scripts\python.exe'
    $pythonExe = if (Test-Path $venvPython) { $venvPython } else { 'python' }
    $serverPy = Join-Path $HubDir 'server.py'
    Write-Host "    Starting hub server: $pythonExe $serverPy"
    $logOut = Join-Path $env:TEMP 'qonclave_hub_stdout.log'
    $logErr = Join-Path $env:TEMP 'qonclave_hub_stderr.log'
    Start-Process -FilePath $pythonExe -ArgumentList $serverPy -WorkingDirectory $RepoDir `
        -WindowStyle Hidden -RedirectStandardOutput $logOut -RedirectStandardError $logErr

    $deadline = (Get-Date).AddSeconds(20)
    while (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) {
        if ((Get-Date) -gt $deadline) {
            throw "hub server did not start listening on port $Port within 20s. Check $logOut / $logErr."
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Ok "hub server is listening on port $Port (logs: $logOut / $logErr)"
}

# --- 5. Start (or reuse) the ngrok tunnel --------------------------------------
Write-Step "Starting ngrok tunnel"
$existing = $null
try {
    $tunnels = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -ErrorAction Stop
    $existing = $tunnels.tunnels | Where-Object { $_.config.addr -match ":$Port$" } | Select-Object -First 1
} catch { }

if ($existing) {
    Write-Ok "reusing existing tunnel"
    $publicUrl = $existing.public_url
} else {
    $logPath = Join-Path $env:TEMP 'ngrok.log'
    Start-Process -FilePath $ngrokExe -ArgumentList @('http', "$Port", '--log=stdout') `
        -WindowStyle Hidden -RedirectStandardOutput $logPath -RedirectStandardError $logPath

    $publicUrl = $null
    $deadline = (Get-Date).AddSeconds(20)
    while (-not $publicUrl) {
        if ((Get-Date) -gt $deadline) {
            throw "ngrok did not report a public URL within 20s. Check $logPath and https://dashboard.ngrok.com."
        }
        Start-Sleep -Milliseconds 500
        try {
            $tunnels = Invoke-RestMethod -Uri 'http://127.0.0.1:4040/api/tunnels' -ErrorAction Stop
            $match = $tunnels.tunnels | Where-Object { $_.config.addr -match ":$Port$" } | Select-Object -First 1
            if ($match) { $publicUrl = $match.public_url }
        } catch { }
    }
    Write-Ok "tunnel started"
}

# --- 6. Report --------------------------------------------------------------
Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host " Hub is reachable at:  $publicUrl" -ForegroundColor Green
Write-Host " Dashboard:            $publicUrl/user/dashboard" -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green
Write-Warn "No authentication on any route - anyone with this URL can reach /edge/event, camera frames, and the dashboard. Treat it as a secret."
Write-Warn "Free-tier ngrok shows a one-time 'Visit Site' interstitial to browsers (not to device/API clients) - click through it."
