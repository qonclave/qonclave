<#
    setup_project.ps1 - clone Qonclave, install its deps, and run the hub.

    Uses the GenieX venv's python (so geniex is importable and reasoning works).
    Idempotent: re-clones only if the repo folder is missing; re-installs deps
    (fast when already satisfied).

    Steps:
      1. Resolve the GenieX venv python (activate the env).
      2. git clone https://github.com/jogendar/Qonclave.git  (skip if present)
      3. cd Qonclave
      4. install hub/requirements.txt into the venv
      5. run hub/server.py

    Usage:
        # Standalone (auto-finds .\geniex-env next to this script):
        powershell -ExecutionPolicy Bypass -File .\setup_project.ps1

        # Or point it at a specific venv python and/or workdir:
        .\setup_project.ps1 -VenvPython C:\path\geniex-env\Scripts\python.exe -WorkDir C:\work

    Extra args after "--" are passed through to server.py, e.g.:
        .\setup_project.ps1 -- --verbose --port 8080
#>

param(
    [string]$VenvPython = "",
    [string]$WorkDir    = $PSScriptRoot,
    [string]$RepoUrl    = "https://github.com/jogendar/Qonclave.git",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ServerArgs
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }

# --- 1. Resolve the GenieX venv python -------------------------------------
Write-Step "Resolving GenieX venv python (activating env)"
if (-not $VenvPython) {
    # Look for geniex-env next to this script (that's where setup_geniex.ps1 puts it).
    $candidate = Join-Path $PSScriptRoot 'geniex-env\Scripts\python.exe'
    if (Test-Path $candidate) { $VenvPython = $candidate }
}
if (-not $VenvPython -or -not (Test-Path $VenvPython)) {
    throw ("GenieX venv python not found. Run setup_geniex.ps1 first, or pass " +
           "-VenvPython <path to geniex-env\Scripts\python.exe>.")
}
# "Activate" the env for this session so `python` resolves to the venv too.
$activate = Join-Path (Split-Path $VenvPython) 'Activate.ps1'
if (Test-Path $activate) { & $activate }
Write-Ok "venv python: $VenvPython"

# --- 2. git clone (skip if already present) --------------------------------
Write-Step "Cloning Qonclave"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git not found on PATH. Run setup_geniex.ps1 first (it installs Git)."
}
$RepoDir = Join-Path $WorkDir 'Qonclave'
if (Test-Path (Join-Path $RepoDir '.git')) {
    Write-Ok "repo already cloned at $RepoDir (pulling latest)"
    git -C $RepoDir pull --ff-only 2>&1 | Out-Host
} else {
    git clone $RepoUrl $RepoDir 2>&1 | Out-Host
    if (-not (Test-Path (Join-Path $RepoDir '.git'))) { throw "git clone failed" }
    Write-Ok "cloned to $RepoDir"
}

# --- 3. cd into repo -------------------------------------------------------
Write-Step "Entering repo"
Set-Location $RepoDir
Write-Ok (Get-Location).Path

# --- 4. install requirements -----------------------------------------------
Write-Step "Installing hub requirements into the venv"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $RepoDir 'hub\requirements.txt')
Write-Ok "requirements installed"

# --- 5. run the hub server -------------------------------------------------
Write-Step "Starting hub server"
Write-Host "    (Ctrl+C to stop; pass server flags after -- e.g. --verbose --port 8080)"
if ($ServerArgs) {
    & $VenvPython (Join-Path $RepoDir 'hub\server.py') @ServerArgs
} else {
    & $VenvPython (Join-Path $RepoDir 'hub\server.py')
}
