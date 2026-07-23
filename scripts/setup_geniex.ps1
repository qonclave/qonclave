<#
    setup_geniex.ps1  —  Snapdragon X (Windows ARM64) bootstrap for GenieX

    Run this at the start of every fresh cloud session. It is idempotent:
    already-installed steps are skipped, so re-runs are quick.

    What it does:
      1. Installs Git CLI (via winget) if missing.
      2. Installs ARM64 Python 3.13.3 (per https://geniex.aihub.qualcomm.com/en/run/python/install) if missing.
      3. Confirms the interpreter is ARM64 (not AMD64/x86_64 — GenieX has no x64 wheel).
      4. Creates the geniex-env virtual environment and installs `geniex` from PyPI.
      5. Verifies the install by importing geniex and printing its version.

    Usage (from an elevated or normal PowerShell prompt):
        powershell -ExecutionPolicy Bypass -File .\setup_geniex.ps1

    By default, after the GenieX env is ready this chains into setup_project.ps1
    to clone Qonclave, install its requirements, and run the hub server.
      -NoProject            stop after the GenieX env (don't clone/run project)
      -ProjectArgs a,b,c    forwarded to setup_project.ps1 (e.g. server flags
                            after --, or -WorkDir). Example:
        .\setup_geniex.ps1 -ProjectArgs '--','--verbose','--port','8080'
#>

param(
    [switch]$NoProject,
    [string[]]$ProjectArgs = @()
)

$ErrorActionPreference = 'Stop'
# PowerShell 7.4+ turns a native command's non-zero exit code into a TERMINATING
# error when ErrorActionPreference is 'Stop'. This script intentionally probes
# commands that may fail (e.g. `py -3.10` when only 3.13 is installed) and checks
# $LASTEXITCODE itself, so opt out of that coupling.
$PSNativeCommandUseErrorActionPreference = $false

# ---- Config ---------------------------------------------------------------
$PythonVersion   = '3.13.3'
$PythonUrl       = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-arm64.exe"
$VenvDir         = Join-Path $PSScriptRoot 'geniex-env'
$MinPythonMajor  = 3
$MinPythonMinor  = 10
# ---------------------------------------------------------------------------

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# --- 0. Sanity: this must be an ARM64 machine ------------------------------
Write-Step "Checking machine architecture"
$arch = (Get-CimInstance Win32_Processor).Architecture
# 12 = ARM64 in Win32_Processor.Architecture
$osArch = $env:PROCESSOR_ARCHITECTURE
Write-Host "    PROCESSOR_ARCHITECTURE = $osArch"
if ($osArch -notmatch 'ARM64') {
    Write-Warn "This does not look like an ARM64 host. GenieX only ships ARM64 wheels; continuing anyway."
}

# --- 1. Git CLI ------------------------------------------------------------
Write-Step "Ensuring Git CLI is installed"
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Ok "git already present: $(git --version)"
} else {
    Write-Host "    Installing Git via winget..."
    winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
    # Refresh PATH for the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    if (Get-Command git -ErrorAction SilentlyContinue) { Write-Ok "git installed: $(git --version)" }
    else { throw "git install did not surface on PATH. Open a new shell and re-run." }
}

# --- 2. ARM64 Python 3.13.3 ------------------------------------------------
Write-Step "Ensuring ARM64 Python $PythonVersion is installed"

function Test-ArmPython($exe) {
    # Returns $true if $exe is a REAL ARM64 python >= min version. Never uses PATH.
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    # Skip the Microsoft Store "App execution alias" stub — it is NOT python; it
    # just prints "Python was not found; run without arguments to install from
    # the Microsoft Store" and exits. Lives under ...\WindowsApps\.
    if ($exe -match '\\WindowsApps\\') { return $false }
    $machine = (& $exe -c "import platform; print(platform.machine())" 2>$null)
    $ver     = (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $machine -or -not $ver) { return $false }
    if ($machine -notmatch 'ARM64') { return $false }
    $mj,$mn = $ver.Split('.')
    return ([int]$mj -gt $MinPythonMajor -or ([int]$mj -eq $MinPythonMajor -and [int]$mn -ge $MinPythonMinor))
}

function Get-ArmPython {
    # Resolve python.exe by ABSOLUTE PATH, not PATH env var (which is stale right
    # after a fresh install). Scans known install roots + py launcher + PATH, and
    # ignores the Microsoft Store stub.
    $candidates = @()

    # 1. py launcher (if present) — ask it where the interpreter lives.
    #    Use `-3` (any Python 3); version filtering happens in Test-ArmPython.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p.Trim() }
    }

    # 2. Well-known per-user / all-users install roots (glob for python.exe)
    $roots = @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python*",
        "${env:ProgramFiles(Arm)}\Python*",
        "C:\Python*"
    )
    foreach ($root in $roots) {
        if ($root -and (Test-Path $root)) {
            $candidates += (Get-ChildItem -Path $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue |
                            Select-Object -ExpandProperty FullName)
        }
    }

    # 3. Whatever is on PATH, last resort (excluding the Store stub below)
    foreach ($c in (Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        if ($c.Source) { $candidates += $c.Source }
    }

    foreach ($exe in ($candidates | Where-Object { $_ -and $_ -notmatch '\\WindowsApps\\' } | Select-Object -Unique)) {
        if (Test-ArmPython $exe) { return $exe }
    }
    return $null
}

$pythonExe = Get-ArmPython
if ($pythonExe) {
    Write-Ok "ARM64 Python found: $pythonExe"
} else {
    Write-Host "    Downloading ARM64 Python installer: $PythonUrl"
    $installer = Join-Path $env:TEMP "python-$PythonVersion-arm64.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $installer
    Write-Host "    Running silent install (per-user, adds to PATH)..."
    Start-Process -FilePath $installer -ArgumentList `
        '/quiet','InstallAllUsers=0','PrependPath=1','Include_launcher=1','Include_pip=1' -Wait
    # Refresh PATH for current session (best-effort; we resolve by absolute path anyway)
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    # Re-scan install folders directly — PATH is often stale immediately post-install.
    $pythonExe = Get-ArmPython
    if (-not $pythonExe) {
        throw ("ARM64 Python still not detected after install. Expected under " +
               "$env:LOCALAPPDATA\Programs\Python. Open a NEW PowerShell window and re-run this script.")
    }
    Write-Ok "ARM64 Python installed: $pythonExe"
}

# --- 3. Confirm architecture (the check you asked for) ---------------------
Write-Step 'Confirming interpreter architecture'
$machine = (& $pythonExe -c "import platform; print(platform.machine())").Trim()
Write-Host "    python -c ""import platform; print(platform.machine())""  ->  $machine"
if ($machine -notmatch 'ARM64') {
    throw "Python reports '$machine', not ARM64. GenieX will not install. Remove the x64 Python and re-run."
}
Write-Ok "Interpreter is ARM64"

# --- 4. Virtual environment + geniex --------------------------------------
Write-Step "Creating virtual environment at $VenvDir"
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
if (Test-Path $VenvPython) {
    Write-Ok "venv already exists, reusing it"
} else {
    & $pythonExe -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) { throw "venv creation failed: $VenvPython not found" }
    Write-Ok "venv created"
}

Write-Step "Installing geniex into the venv (using its python directly, no PATH)"
# Drive everything through the venv's own python.exe by ABSOLUTE PATH so this
# works even though the venv is not 'activated' on PATH in this session.
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -U geniex

# --- 5. Verify -------------------------------------------------------------
Write-Step "Verifying geniex install"
& $VenvPython -c "import platform, geniex; print('machine:', platform.machine()); print('geniex version:', geniex.version())"

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host " GenieX environment ready." -ForegroundColor Green
Write-Host " Run scripts either by activating the venv:" -ForegroundColor Green
Write-Host "     .\geniex-env\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "     python scripts\test_geniex.py" -ForegroundColor Green
Write-Host " ...or without activating, via the venv python directly:" -ForegroundColor Green
Write-Host "     .\geniex-env\Scripts\python.exe scripts\test_geniex.py" -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green

# --- 6. Chain into project setup (clone + install + run the hub) -----------
# Skip with:  setup_geniex.ps1 -NoProject
if (-not $NoProject) {
    $projectScript = Join-Path $PSScriptRoot 'setup_project.ps1'
    if (Test-Path $projectScript) {
        Write-Step "Handing off to setup_project.ps1 (clone + install + run hub)"
        & $projectScript -VenvPython $VenvPython @ProjectArgs
    } else {
        Write-Warn "setup_project.ps1 not found next to this script; skipping project setup."
    }
}
