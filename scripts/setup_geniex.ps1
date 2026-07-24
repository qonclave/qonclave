<#
    setup_geniex.ps1  -  Snapdragon X (Windows ARM64) bootstrap for GenieX + hub

    Run this at the start of every fresh cloud session, from inside an already
    git-synced Qonclave checkout (this script lives at Qonclave/scripts/). It
    is idempotent: already-installed steps are skipped, so re-runs are quick.

    This script does NOT clone or pull the repo - sync git yourself first
    (git clone / git pull), then run this script from inside that checkout.

    What it does:
      1. Installs Git CLI (via winget) if missing.
      2. Ensures ARM64 Python 3.13.3 exists (per https://geniex.aihub.qualcomm.com/en/run/python/install).
         Version-agnostic to whatever is already on the box: an older Python
         (e.g. 3.9), a newer one, or an x64 build is ignored (never reused,
         never removed) and 3.13.3 is installed fresh, side-by-side.
      3. Confirms the interpreter is ARM64 (not AMD64/x86_64 - GenieX has no x64 wheel).
      4. Creates the geniex-env virtual environment FROM that exact Python
         version and installs `geniex` from PyPI into it.
      5. Verifies the install by importing geniex and printing its version.
      6. Installs hub/requirements.txt (from this checkout) into the venv.
      7. Runs hub/server.py.

    Usage (from an elevated or normal PowerShell prompt, inside the checkout):
        powershell -ExecutionPolicy Bypass -File .\scripts\setup_geniex.ps1

      -NoRun            stop after installing requirements; don't start the server
      -- a b c          extra args forwarded to hub/server.py, e.g.:
        .\scripts\setup_geniex.ps1 -- --verbose --port 8080
#>

param(
    [switch]$NoRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ServerArgs
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
# Pinned Git for Windows ARM64 build, used only as a fallback when winget
# itself can't run (see step 1) - not "latest", so this stays reproducible.
$GitVersion      = '2.55.0.3'
$GitTag          = 'v2.55.0.windows.3'
$GitUrl          = "https://github.com/git-for-windows/git/releases/download/$GitTag/Git-$GitVersion-arm64.exe"
$VenvDir         = Join-Path $PSScriptRoot 'geniex-env'
# Exact major.minor required, derived from $PythonVersion (e.g. "3.13.3" -> 3, 13).
# Any OTHER Python found on the box - older OR newer, ARM64 or not - is ignored;
# this script always ensures exactly this minor version is installed and uses
# it to build the venv, so behavior is agnostic to whatever's already there.
$RequiredMajor, $RequiredMinor = $PythonVersion.Split('.')[0..1] | ForEach-Object { [int]$_ }
# This script lives at <repo>/scripts/setup_geniex.ps1; the repo root is its parent.
$RepoDir = Split-Path $PSScriptRoot -Parent
# ---------------------------------------------------------------------------

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

# --- 0. Sanity: this must be an ARM64 machine, running from inside a checkout
Write-Step "Checking machine architecture"
$osArch = $env:PROCESSOR_ARCHITECTURE
Write-Host "    PROCESSOR_ARCHITECTURE = $osArch"
if ($osArch -notmatch 'ARM64') {
    Write-Warn "This does not look like an ARM64 host. GenieX only ships ARM64 wheels; continuing anyway."
}

Write-Step "Checking for a synced Qonclave checkout"
$ServerPy = Join-Path $RepoDir 'hub\server.py'
if (-not (Test-Path $ServerPy)) {
    throw ("hub\server.py not found under $RepoDir. This script assumes the repo is " +
           "already git-synced and that this script is at <repo>\scripts\setup_geniex.ps1. " +
           "Run 'git clone https://github.com/jogendar/Qonclave.git' (or 'git pull' in an " +
           "existing checkout) first, then re-run this script from inside it.")
}
Write-Ok "found checkout at $RepoDir"

# --- 1. Git CLI ------------------------------------------------------------
Write-Step "Ensuring Git CLI is installed"
if (Get-Command git -ErrorAction SilentlyContinue) {
    Write-Ok "git already present: $(git --version)"
} else {
    Write-Host "    Installing Git via winget..."
    $wingetOk = $true
    try {
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { $wingetOk = $false }
    } catch {
        # On a brand-new machine, `winget.exe` on PATH can be an unprovisioned
        # Microsoft Store "App Execution Alias" stub that fails with
        # "The file cannot be accessed by the system" - not a real winget
        # error, just winget being unusable yet. Fall back to a direct
        # installer download instead of hard-failing the whole bootstrap.
        Write-Warn "winget failed to run ($($_.Exception.Message)); falling back to a direct Git installer download."
        $wingetOk = $false
    }

    if (-not $wingetOk) {
        Write-Host "    Downloading Git for Windows ARM64 installer: $GitUrl"
        $gitInstaller = Join-Path $env:TEMP "Git-$GitVersion-arm64.exe"
        Invoke-WebRequest -Uri $GitUrl -OutFile $gitInstaller
        Write-Host "    Running silent install..."
        Start-Process -FilePath $gitInstaller -ArgumentList '/VERYSILENT', '/NORESTART' -Wait
    }

    # Refresh PATH for the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    if (Get-Command git -ErrorAction SilentlyContinue) { Write-Ok "git installed: $(git --version)" }
    else { throw "git install did not surface on PATH. Open a new shell and re-run." }
}

# --- 2. ARM64 Python (exact required version) ------------------------------
Write-Step "Ensuring ARM64 Python $PythonVersion is installed"

function Test-ArmPython($exe) {
    # Returns $true only if $exe is a REAL ARM64 python matching the EXACT
    # required major.minor (not just ">= some minimum"). This makes the script
    # agnostic to whatever Python already happens to be on the box: an older
    # 3.9, a newer 3.14, an x64 build, or the Store stub are all rejected the
    # same way, and a fresh install of the required version is triggered.
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    # Skip the Microsoft Store "App execution alias" stub - it is NOT python; it
    # just prints "Python was not found; run without arguments to install from
    # the Microsoft Store" and exits. Lives under ...\WindowsApps\.
    if ($exe -match '\\WindowsApps\\') { return $false }
    $machine = (& $exe -c "import platform; print(platform.machine())" 2>$null)
    $ver     = (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $machine -or -not $ver) { return $false }
    if ($machine -notmatch 'ARM64') { return $false }
    $mj,$mn = $ver.Split('.') | ForEach-Object { [int]$_ }
    return ($mj -eq $RequiredMajor -and $mn -eq $RequiredMinor)
}

function Get-ArmPython {
    # Resolve python.exe by ABSOLUTE PATH, not PATH env var (which is stale right
    # after a fresh install). Scans known install roots + py launcher + PATH, and
    # ignores the Microsoft Store stub. Only returns a match for the exact
    # required version (see Test-ArmPython) - any other installed Python,
    # older or newer, is treated as absent and triggers a fresh install below.
    $candidates = @()

    # 1. py launcher (if present) - ask it where the interpreter lives.
    #    Use `-3` (any Python 3); version filtering happens in Test-ArmPython.
    #    Do NOT ask for the exact version (e.g. "-3.13") here: when that exact
    #    version isn't installed, some py.exe builds print "No suitable Python
    #    runtime found" via a direct console write that bypasses PowerShell's
    #    stderr redirection (2>$null doesn't catch it), which is confusing
    #    noise even though it's harmless and already handled via $LASTEXITCODE.
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
    Write-Ok "ARM64 Python $PythonVersion found: $pythonExe"
} else {
    Write-Host "    No ARM64 Python $RequiredMajor.$RequiredMinor found (an older/newer/x64 Python may be" -ForegroundColor Yellow
    Write-Host "    present but is ignored) - installing $PythonVersion fresh, side-by-side." -ForegroundColor Yellow
    Write-Host "    Downloading ARM64 Python installer: $PythonUrl"
    $installer = Join-Path $env:TEMP "python-$PythonVersion-arm64.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $installer
    Write-Host "    Running silent install (per-user, adds to PATH)..."
    $installArgs = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_pip=1')
    Start-Process -FilePath $installer -ArgumentList $installArgs -Wait
    # Refresh PATH for current session (best-effort; we resolve by absolute path anyway)
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    # Re-scan install folders directly - PATH is often stale immediately post-install.
    $pythonExe = Get-ArmPython
    if (-not $pythonExe) {
        throw ("ARM64 Python $PythonVersion still not detected after install. Expected under " +
               "$env:LOCALAPPDATA\Programs\Python. Open a NEW PowerShell window and re-run this script.")
    }
    Write-Ok "ARM64 Python $PythonVersion installed: $pythonExe"
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
$VenvCfg    = Join-Path $VenvDir 'pyvenv.cfg'

function Test-VenvComplete {
    # A venv is only usable if BOTH python.exe and pyvenv.cfg exist. python.exe
    # looks for pyvenv.cfg next to itself (or one dir up) to know it's running
    # inside a venv; a missing pyvenv.cfg ("no pyvenv.cfg file" errors) means
    # venv creation was interrupted/partial, even if python.exe got copied.
    (Test-Path $VenvPython) -and (Test-Path $VenvCfg)
}

if ((Test-VenvComplete) -and (Test-ArmPython $VenvPython)) {
    Write-Ok "venv already exists and matches Python $PythonVersion, reusing it"
} else {
    if (Test-Path $VenvDir) {
        Write-Warn "Existing venv at $VenvDir is missing/incomplete or built from a different Python version; rebuilding it."
        Remove-Item -Recurse -Force $VenvDir
    }
    & $pythonExe -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "venv creation failed (python -m venv exited $LASTEXITCODE). Check disk space and permissions for $VenvDir."
    }
    if (-not (Test-VenvComplete)) {
        $missing = if (-not (Test-Path $VenvPython)) { $VenvPython } else { $VenvCfg }
        throw "venv creation reported success but is incomplete: $missing is missing. Delete $VenvDir and re-run."
    }
    Write-Ok "venv created from $pythonExe"
}

Write-Step "Installing geniex into the venv (using its python directly, no PATH)"
# Drive everything through the venv's own python.exe by ABSOLUTE PATH so this
# works even though the venv is not 'activated' on PATH in this session.
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -U geniex

# --- 5. Verify geniex --------------------------------------------------------
Write-Step "Verifying geniex install"
& $VenvPython -c "import platform, geniex; print('machine:', platform.machine()); print('geniex version:', geniex.version())"

# --- 6. Install hub requirements --------------------------------------------
Write-Step "Installing hub requirements into the venv"
& $VenvPython -m pip install -r (Join-Path $RepoDir 'hub\requirements.txt')
Write-Ok "requirements installed"

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host " GenieX environment ready." -ForegroundColor Green
Write-Host " Run scripts either by activating the venv:" -ForegroundColor Green
Write-Host "     .\scripts\geniex-env\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "     python hub\server.py" -ForegroundColor Green
Write-Host " ...or without activating, via the venv python directly:" -ForegroundColor Green
Write-Host "     .\scripts\geniex-env\Scripts\python.exe hub\server.py" -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green

# --- 7. Run the hub server ---------------------------------------------------
if ($NoRun) {
    Write-Step "NoRun set - skipping server start"
} else {
    Write-Step "Starting hub server"
    Write-Host "    (Ctrl+C to stop; pass server flags after -- e.g. --verbose --port 8080)"
    Set-Location $RepoDir
    if ($ServerArgs) {
        & $VenvPython (Join-Path $RepoDir 'hub\server.py') @ServerArgs
    } else {
        & $VenvPython (Join-Path $RepoDir 'hub\server.py')
    }
}
