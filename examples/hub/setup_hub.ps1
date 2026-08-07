<#
    setup_hub.ps1  -  Windows bootstrap for the Qonclave hub (GenieX on Snapdragon X)

    Run this at the start of every fresh session, from inside an already
    git-synced Qonclave checkout (this script lives at Qonclave/hub/). It
    is idempotent: already-installed steps are skipped, so re-runs are quick.

    ARM64 (Snapdragon X) vs. other hosts:
      * On Windows ARM64 it installs the exact ARM64 Python 3.13.3 and GenieX,
        enabling on-device VLM reasoning.
      * On a regular x86-64 (AMD64) laptop it REUSES the Python already on the
        machine (any 3.10+), skips the ARM64 Python download and skips GenieX
        (which ships ARM64-only wheels). The hub still runs end-to-end - the
        VLM just reports "unavailable" and reasoning is disabled; face-ID uses
        its CPU path, and everything else (Flask, MQTT, SMS, enrollment) works.

    This script does NOT clone or pull the repo - sync git yourself first
    (git clone / git pull), then run this script from inside that checkout.

    What it does:
      1. Installs Git CLI (via winget) if missing.
         Installs Node.js + Claude CLI (via npm) if missing.
      2. Ensures a usable Python: ARM64 3.13.3 on Snapdragon X (per
         https://geniex.aihub.qualcomm.com/en/run/python/install), or reuses
         any existing Python 3.10+ on a non-ARM host (installing the amd64
         3.13.3 build only if none is found).
      3. Confirms the interpreter arch (ARM64 required only on Snapdragon X).
      4. Creates the geniex-env virtual environment FROM that Python and, on
         ARM64 only, installs `geniex` from PyPI into it.
      5. On ARM64, verifies the install by importing geniex and printing its version.
      6. Installs hub/requirements.txt (from this checkout) into the venv.
      6a. Installs the in-tree Qonclave SDK (framework/sdk/python) editable into
         the same venv, so `import qonclave` works for hub/server.py.
      6b. Exports the pose model (hub/framework/pose/setup/setup_pose.ps1) into
         that same venv. Needs an AI Hub token; -SkipPose opts out and the hub
         reports pose as unavailable.
      7. Installs face ID (hub/framework/face_id/setup/setup.ps1) into that same venv, since
         hub/server.py imports framework.face_id.identity in-process. Skipped when
         already installed, so re-runs stay quick.
      8. Runs hub/server.py.

    Usage (from an elevated or normal PowerShell prompt, inside the checkout):
        powershell -ExecutionPolicy Bypass -File .\examples\hub\setup_hub.ps1

      -NoRun            stop after installing requirements; don't start the server
      -Warmup           pre-load the VLM model at server start (default: off, loads lazily on first request)
      -SkipFaceId       don't install face ID at all (hub still runs; face-ID
                        reports "not_enabled")
      -SkipPose         don't export the pose model (hub still runs; pose
                        reports "unavailable"). Pose needs an AI Hub token,
                        so this is the switch for a machine without one.
      -HrnetPoseJobId   reuse an already-completed AI Hub compile job for the
                        pose model instead of recompiling.
      -AiHubToken       Qualcomm AI Hub token for the ARM64 NPU model export.
                        When omitted, this script prompts once and reuses the
                        token for both face-ID and pose setup. Free at
                        https://workbench.aihub.qualcomm.com
                        (Account -> Settings -> API Token).
      -MediaPipeFaceJobId / -CavaFaceJobId
                        reuse already-completed AI Hub compile jobs instead of
                        recompiling - see hub/framework/face_id/README.md.
                        Passing BOTH also skips installing qai-hub-models and
                        torch: they are the exporter and the CPU embedder, and
                        NPU inference needs neither. Trade-off: no CPU embedder
                        to fall back on if CavaFace.onnx later goes missing.
      -Internal         resolve all pip installs (here and in the face ID
                        setup scripts it calls) through Qualcomm's internal
                        devpi mirror instead of pypi.org - use this on
                        networks where files.pythonhosted.org is unreachable.
      -- a b c          extra args forwarded to server.py, e.g.:
        .\examples\hub\setup_hub.ps1 -- --verbose --port 8080
#>

param(
    [switch]$NoRun,
    [switch]$Warmup,
    [switch]$SkipFaceId,
    [switch]$SkipPose,
    [string]$AiHubToken = '',
    [string]$MediaPipeFaceJobId = '',
    [string]$CavaFaceJobId = '',
    [string]$HrnetPoseJobId = '',
    [switch]$Internal,
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
$PythonVersion   = '3.12.10'
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
# This script lives at <repo>/examples/hub/setup_hub.ps1; the repo root is two levels up.
$RepoDir = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
# -Internal routes every pip install (here and in the face ID setup scripts
# this one calls) through Qualcomm's internal devpi mirror instead of
# pypi.org/files.pythonhosted.org, for networks that can't reach the latter.
$PipIndexArgs = if ($Internal) {
    @('--trusted-host', 'devpi.qualcomm.com', '-i', 'https://devpi.qualcomm.com/root/pypi/+simple/')
} else {
    @()
}
# ---------------------------------------------------------------------------

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    [ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    [!]  $msg" -ForegroundColor Yellow }

function Request-AiHubToken([string]$CurrentToken) {
    if ($CurrentToken) { return $CurrentToken }

    Write-Host ""
    Write-Host "Qualcomm AI Hub token required for model export/download." -ForegroundColor Cyan
    Write-Host "Get your token: https://workbench.aihub.qualcomm.com (Account -> Settings -> API Token)" -ForegroundColor Cyan
    $token = Read-Host "Enter your AI Hub API token"
    if (-not $token) { throw "No AI Hub token provided." }
    return $token
}

# --- 0. Sanity: this must be an ARM64 machine, running from inside a checkout
Write-Step "Checking machine architecture"
# Read this from the registry, not $env:PROCESSOR_ARCHITECTURE - the env var
# reflects the CALLING PROCESS's architecture, so a powershell.exe running
# under x64 emulation on a Snapdragon X box reports AMD64 and this script
# would mistake an ARM64 host for x86. That matters twice: the warning below,
# and the face-ID model probe in step 6b, which only requires the exported
# .onnx files on ARM64. examples\hub\framework\face_id\setup\setup.ps1 reads the same registry value.
$osArch = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').PROCESSOR_ARCHITECTURE
$IsArm  = ($osArch -match 'ARM64')
Write-Host "    PROCESSOR_ARCHITECTURE (OS) = $osArch"
if (-not $IsArm) {
    Write-Warn "Not an ARM64 host. GenieX (VLM reasoning) ships ARM64-only wheels, so it will be"
    Write-Warn "skipped; the hub still runs and face-ID uses its CPU path. This script will reuse"
    Write-Warn "the Python already installed on this machine instead of fetching an ARM64 build."
}

Write-Step "Checking for a synced Qonclave checkout"
$ServerPy = Join-Path $RepoDir 'examples\hub\server.py'
if (-not (Test-Path $ServerPy)) {
    throw ("examples\hub\server.py not found under $RepoDir. This script assumes the repo is " +
           "already git-synced and that this script is at <repo>\examples\hub\setup_hub.ps1. " +
           "Run 'git clone https://github.com/qonclave/qonclave.git' (or 'git pull' in an " +
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




Write-Step "Ensuring Python $PythonVersion is installed"

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
    # sysconfig.get_platform() reflects how THIS interpreter was actually
    # compiled ('win-arm64' vs 'win-amd64'), unlike platform.machine() - which
    # on Windows reads the OS's native architecture (via PROCESSOR_ARCHITEW6432
    # under WOW64), so an x64 Python running under emulation on an ARM64 box
    # still reports 'ARM64' there too. Relying on machine() alone lets an x64
    # interpreter (e.g. an Anaconda/Miniconda install on PATH) be mistaken for
    # the real ARM64 build, producing a venv that can never install win_arm64
    # wheels.
    $platformTag = (& $exe -c "import sysconfig; print(sysconfig.get_platform())" 2>$null)
    $ver         = (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $platformTag -or -not $ver) { return $false }
    if ($platformTag -notmatch 'arm64') { return $false }
    $mj,$mn = $ver.Split('.') | ForEach-Object { [int]$_ }
    return ($mj -eq $RequiredMajor -and $mn -eq $RequiredMinor)
}

function Test-AnyPython($exe) {
    # Non-ARM hosts: accept whatever usable Python 3 is already installed
    # (>= 3.10, any 64-bit arch). We're not installing GenieX here, so we don't
    # need the exact ARM64 3.13.3 - just a Python that can run Flask + the CPU
    # face-ID stack. The Store stub and unreadable paths are still rejected.
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    if ($exe -match '\\WindowsApps\\') { return $false }
    $ver = (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $ver) { return $false }
    $mj,$mn = $ver.Split('.') | ForEach-Object { [int]$_ }
    return ($mj -eq 3 -and $mn -ge 10)
}

function Get-Python {
    # Resolve python.exe by ABSOLUTE PATH, not PATH env var (which is stale right
    # after a fresh install). Probes each source in turn and RETURNS ON THE FIRST
    # acceptable interpreter, so a hit from the fast `py` launcher never triggers
    # the slower filesystem scans below. On ARM64 only the exact ARM64 3.13.3 is
    # accepted (GenieX needs it); on other hosts any usable Python 3.10+ is fine.
    function _accepts($exe) {
        if (-not $exe -or ($exe -match '\\WindowsApps\\')) { return $false }
        if ($IsArm) { return (Test-ArmPython $exe) } else { return (Test-AnyPython $exe) }
    }

    # 1. py launcher (if present) - ask it where the interpreter lives. This is
    #    the fast path and usually the only one that runs.
    #    Use `-3` (any Python 3); version filtering happens in the acceptor.
    #    Do NOT ask for the exact version (e.g. "-3.13") here: when that exact
    #    version isn't installed, some py.exe builds print "No suitable Python
    #    runtime found" via a direct console write that bypasses PowerShell's
    #    stderr redirection (2>$null doesn't catch it), which is confusing
    #    noise even though it's harmless and already handled via $LASTEXITCODE.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $p = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p -and (_accepts $p.Trim())) { return $p.Trim() }
    }

    # 2. Well-known per-user / all-users install roots. A CPython install puts
    #    python.exe directly in its version folder (e.g. ...\Python313\python.exe),
    #    so -Depth 1 finds it WITHOUT recursing into Lib\site-packages (which can
    #    hold tens of thousands of files and is what makes a full -Recurse hang).
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
                if (_accepts $f) { return $f }
            }
        }
    }

    # 3. Whatever is on PATH, last resort (excluding the Store stub).
    foreach ($c in (Get-Command python.exe -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -and (_accepts $c.Source)) { return $c.Source }
    }

    return $null
}

$pythonExe = Get-Python
if ($pythonExe) {
    if ($IsArm) { Write-Ok "ARM64 Python $PythonVersion found: $pythonExe" }
    else        { Write-Ok "Reusing existing Python: $pythonExe" }
} elseif (-not $IsArm) {
    # Non-ARM and nothing usable found: install the stock x86-64 build of the
    # same version, side-by-side. (The ARM64 installer URL would be wrong here.)
    $x64Url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
    Write-Host "    No usable Python 3.10+ found - installing $PythonVersion (amd64) fresh, side-by-side." -ForegroundColor Yellow
    Write-Host "    Downloading Python installer: $x64Url"
    $installer = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
    Invoke-WebRequest -Uri $x64Url -OutFile $installer
    Write-Host "    Running silent install (per-user, adds to PATH)..."
    $installArgs = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_pip=1')
    Start-Process -FilePath $installer -ArgumentList $installArgs -Wait
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    $pythonExe = Get-Python
    if (-not $pythonExe) {
        throw ("Python $PythonVersion still not detected after install. Expected under " +
               "$env:LOCALAPPDATA\Programs\Python. Open a NEW PowerShell window and re-run this script.")
    }
    Write-Ok "Python $PythonVersion installed: $pythonExe"
} else {
    Write-Host "    No ARM64 Python $RequiredMajor.$RequiredMinor found (an older/newer/x64 Python may be" -ForegroundColor Yellow
    Write-Host "    present but is ignored) - installing $PythonVersion fresh, side-by-side." -ForegroundColor Yellow
    Write-Host "    Downloading ARM64 Python installer: $PythonUrl"
    $installer = Join-Path $env:TEMP "python-$PythonVersion-arm64.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $installer
    Write-Host "    Running silent install (per-user, adds to PATH)..."
    # Include_launcher=0: on a locked-down box (WDAC/Software Restriction
    # Policy enforced), the py-launcher package's shell-extension registration
    # (ARM64_SHELLEXT) can get rejected by policy (MSI error 1625) even though
    # every other component (core/exe/dev/lib/test/doc/tcltk) installs fine -
    # and because the installer bundle is atomic, that one failure rolls back
    # the entire install. The launcher is a convenience `py` command only;
    # Get-Python resolves the interpreter by absolute path and doesn't need it.
    $installArgs = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=0', 'Include_pip=1')
    Start-Process -FilePath $installer -ArgumentList $installArgs -Wait
    # Refresh PATH for current session (best-effort; we resolve by absolute path anyway)
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    # Re-scan install folders directly - PATH is often stale immediately post-install.
    $pythonExe = Get-Python
    if (-not $pythonExe) {
        throw ("ARM64 Python $PythonVersion still not detected after install. Expected under " +
               "$env:LOCALAPPDATA\Programs\Python. Open a NEW PowerShell window and re-run this script.")
    }
    Write-Ok "ARM64 Python $PythonVersion installed: $pythonExe"
}

# --- 3. Confirm architecture -----------------------------------------------
Write-Step 'Confirming interpreter architecture'
$machine = (& $pythonExe -c "import platform; print(platform.machine())").Trim()
Write-Host "    python -c ""import platform; print(platform.machine())""  ->  $machine"
if ($IsArm -and $machine -notmatch 'ARM64') {
    throw "Python reports '$machine', not ARM64. GenieX will not install. Remove the x64 Python and re-run."
}
if ($IsArm) { Write-Ok "Interpreter is ARM64" }
else        { Write-Ok "Interpreter is $machine (non-ARM host; GenieX/VLM will be skipped)" }

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

# On ARM64 the venv must be the exact ARM64 3.13.3 (GenieX); on other hosts any
# usable Python 3.10+ venv is fine.
$venvAcceptable = if ($IsArm) { Test-ArmPython $VenvPython } else { Test-AnyPython $VenvPython }
if ((Test-VenvComplete) -and $venvAcceptable) {
    Write-Ok "venv already exists and matches the required Python, reusing it"
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

Write-Step "Installing Python packages into the venv (using its python directly, no PATH)"
# Drive everything through the venv's own python.exe by ABSOLUTE PATH so this
# works even though the venv is not 'activated' on PATH in this session.
& $VenvPython -m pip install --upgrade pip @PipIndexArgs

if ($IsArm) {
    & $VenvPython -m pip install -U geniex @PipIndexArgs

    # --- 5. Verify geniex ----------------------------------------------------
    Write-Step "Verifying geniex install"
    & $VenvPython -c "import platform, geniex; print('machine:', platform.machine()); print('geniex version:', geniex.version())"
} else {
    Write-Ok "Non-ARM host - skipping GenieX (VLM). The hub runs and reports the VLM as unavailable; reasoning is disabled, everything else works."
}

# --- 6. Install hub requirements --------------------------------------------
Write-Step "Installing hub requirements into the venv"
& $VenvPython -m pip install -r (Join-Path $RepoDir 'examples\hub\requirements.txt') @PipIndexArgs
Write-Ok "requirements installed"

# --- 6a. Install the in-tree Qonclave SDK (editable) -------------------------
# framework/sdk/python is the forward-looking framework package. It is installed
# EDITABLE so edits under framework/ take effect without a reinstall, and by
# ABSOLUTE path because pip resolves relative paths in requirements files against
# the caller's working directory rather than the file's own location - which is
# exactly why this is a step here and not a line in requirements.txt.
#
# Run unconditionally rather than probed: an editable install records an absolute
# path to the source tree, so a checkout that moved would otherwise keep a stale
# working install that imports from the old location.
Write-Step "Installing the Qonclave SDK (editable) into the venv"
$SdkDir = Join-Path $RepoDir 'framework\sdk\python'
& $VenvPython -m pip install -e $SdkDir @PipIndexArgs
if ($LASTEXITCODE -ne 0) {
    throw "qonclave SDK install failed (pip exited $LASTEXITCODE). Expected the package at $SdkDir."
}
& $VenvPython -c "import qonclave.core.models as m; print('    qonclave OK, schema_version', m.SCHEMA_VERSION)"
Write-Ok "qonclave SDK installed"

# --- 6b. Install face ID into the SAME venv ----------------------------------
# hub/server.py imports framework.face_id.identity in-process, so face-ID's dependencies
# must live in this venv - not system Python - or the hub reports face-ID as
# "not_enabled" even after a successful standalone face_id setup.
Write-Step "Installing face ID into the venv"
if ($SkipFaceId) {
    Write-Ok "-SkipFaceId set - skipping (hub will report face-ID as not_enabled)"
} else {
    $FaceIdSetup = Join-Path $RepoDir 'examples\hub\framework\face_id\setup\setup.ps1'
    $FaceIdModels = Join-Path $RepoDir 'examples\hub\framework\face_id\models'

    # Idempotency probe, so re-running this bootstrap every session stays quick:
    # face-ID is already usable if the stack its CHOSEN MODE needs is present in
    # THIS venv and - on ARM64, where the NPU export is mandatory - both exported
    # models exist.
    #
    # Probe per mode, not a fixed list: ARM64 runs both models through
    # onnxruntime-qnn and touches neither mediapipe nor qai_hub_models, and with
    # both -*JobId flags face_id/setup/setup.ps1 deliberately never installs
    # them. Probing for qai_hub_models there would report "not installed" every
    # single run and redo face-ID setup each session. x86 has no NPU path, so it
    # genuinely needs both.
    #
    # find_spec (not `import`) so this doesn't actually load the torch-backed
    # stack just to answer a yes/no question, and everything is wrapped so the
    # probe writes NOTHING to stderr: in Windows PowerShell 5.1, redirecting a
    # native command's stderr (`2>$null`) wraps each line as a NativeCommandError
    # ErrorRecord, which $ErrorActionPreference='Stop' turns into a TERMINATING
    # error - aborting this whole bootstrap just because a module was missing,
    # which is the normal first-run case. Emitting no stderr avoids that entirely.
    $probeModules = if ($osArch -match 'ARM64') {
        '"onnxruntime", "onnxruntime_qnn"'
    } else {
        '"mediapipe", "qai_hub_models"'
    }
    $probe = 'import sys' + "`n" +
             'try:' + "`n" +
             '    from importlib.util import find_spec' + "`n" +
             "    ok = all(find_spec(m) is not None for m in ($probeModules))" + "`n" +
             'except Exception:' + "`n" +
             '    ok = False' + "`n" +
             'sys.exit(0 if ok else 1)'
    & $VenvPython -c $probe
    $depsOk = ($LASTEXITCODE -eq 0)
    $modelsOk = (-not ($osArch -match 'ARM64')) -or (
        (Test-Path (Join-Path $FaceIdModels 'CavaFace.onnx')) -and
        (Test-Path (Join-Path $FaceIdModels 'MediaPipeFace.onnx'))
    )

    if ($depsOk -and $modelsOk) {
        Write-Ok "face ID already installed in this venv, skipping"
        Write-Host "        (re-run examples\hub\framework\face_id\setup\setup.ps1 -PythonPath `"$VenvPython`" to force)"
    } else {
        # Prompt in the parent so the same token can be forwarded to pose
        # setup later in this run instead of each child script prompting.
        if ($osArch -match 'ARM64') {
            $AiHubToken = Request-AiHubToken $AiHubToken
        }
        $faceArgs = @{ PythonPath = $VenvPython }
        if ($AiHubToken)         { $faceArgs.Token              = $AiHubToken }
        if ($MediaPipeFaceJobId) { $faceArgs.MediaPipeFaceJobId = $MediaPipeFaceJobId }
        if ($CavaFaceJobId)      { $faceArgs.CavaFaceJobId      = $CavaFaceJobId }
        if ($Internal)           { $faceArgs.Internal           = $true }

        # Non-fatal: the hub server runs fine without face ID (it degrades to
        # "not_enabled"), and on ARM64 this step can need an AI Hub token /
        # network round-trip. Don't strand the whole bootstrap over it.
        Push-Location (Split-Path $FaceIdSetup -Parent)
        try {
            & $FaceIdSetup @faceArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "face ID setup exited $LASTEXITCODE - hub will report face-ID as not_enabled."
                Write-Warn "Re-run it directly: hub\framework\face_id\setup\setup.ps1 -PythonPath `"$VenvPython`""
            } else {
                Write-Ok "face ID installed"
            }
        } catch {
            Write-Warn "face ID setup failed ($($_.Exception.Message)) - hub will report face-ID as not_enabled."
            Write-Warn "Re-run it directly: hub\framework\face_id\setup\setup.ps1 -PythonPath `"$VenvPython`""
        } finally {
            Pop-Location
        }
    }
}

# --- 6c. Export the pose model into the SAME package tree --------------------
# hub/server.py imports framework.pose.pose in-process; the runtime deps
# (onnxruntime, onnxruntime-qnn, cv2, numpy) are already in this venv, so
# pose setup is only the AI Hub model export + per-host context-binary bake.
# Pose is NPU-only, so this step is ARM64-only (elsewhere the hub reports
# pose as unavailable by design).
Write-Step "Setting up pose estimation (HRNetPose)"
if ($SkipPose) {
    Write-Ok "-SkipPose set - skipping (hub will report pose as unavailable)"
} elseif (-not ($osArch -match 'ARM64')) {
    Write-Ok "non-ARM64 host - skipping (pose runs on Snapdragon hubs only)"
} else {
    $PoseSetup  = Join-Path $RepoDir 'examples\hub\framework\pose\setup\setup_pose.ps1'
    $PoseModels = Join-Path $RepoDir 'examples\hub\framework\pose\models'

    # Idempotency probe: the export is the slow, token-needing part; if the
    # model is already present, only offer the re-run hint.
    if (Test-Path (Join-Path $PoseModels 'hrnet_pose.onnx')) {
        Write-Ok "pose model already present, skipping"
        Write-Host "        (re-run hub\framework\pose\setup\setup_pose.ps1 -PythonPath `"$VenvPython`" to force)"
    } else {
        $AiHubToken = Request-AiHubToken $AiHubToken
        $poseArgs = @{ PythonPath = $VenvPython }
        if ($AiHubToken)     { $poseArgs.Token          = $AiHubToken }
        if ($HrnetPoseJobId) { $poseArgs.HrnetPoseJobId = $HrnetPoseJobId }
        if ($Internal)       { $poseArgs.Internal       = $true }

        # Non-fatal, same policy as face ID: the hub runs fine without pose
        # (it degrades to "unavailable"), and this step can need an AI Hub
        # token / network round-trip.
        Push-Location (Split-Path $PoseSetup -Parent)
        try {
            & $PoseSetup @poseArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "pose setup exited $LASTEXITCODE - hub will report pose as unavailable."
                Write-Warn "Re-run it directly: hub\framework\pose\setup\setup_pose.ps1 -PythonPath `"$VenvPython`""
            } else {
                Write-Ok "pose model installed"
            }
        } catch {
            Write-Warn "pose setup failed ($($_.Exception.Message)) - hub will report pose as unavailable."
            Write-Warn "Re-run it directly: hub\framework\pose\setup\setup_pose.ps1 -PythonPath `"$VenvPython`""
        } finally {
            Pop-Location
        }
    }
}

Write-Host "`n===================================================================" -ForegroundColor Green
if ($IsArm) {
    Write-Host " GenieX environment ready." -ForegroundColor Green
} else {
    Write-Host " Hub environment ready (non-ARM host: VLM/GenieX disabled)." -ForegroundColor Green
}
Write-Host " Run scripts either by activating the venv:" -ForegroundColor Green
Write-Host "     .\examples\hub\geniex-env\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "     python examples\hub\server.py" -ForegroundColor Green
Write-Host " ...or without activating, via the venv python directly:" -ForegroundColor Green
Write-Host "     .\examples\hub\geniex-env\Scripts\python.exe examples\hub\server.py" -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green

# --- 8. Run the hub server ---------------------------------------------------
if ($NoRun) {
    Write-Step "NoRun set - skipping server start"
} else {
    Write-Step "Starting hub server"
    Write-Host "    (Ctrl+C to stop; pass server flags after -- e.g. --verbose --port 8080)"
    if ($Warmup) {
        $env:QONCLAVE_WARMUP = "1"
    }
    Set-Location $RepoDir
    if ($ServerArgs) {
        & $VenvPython (Join-Path $RepoDir 'examples\hub\server.py') @ServerArgs
    } else {
        & $VenvPython (Join-Path $RepoDir 'examples\hub\server.py')
    }
}
