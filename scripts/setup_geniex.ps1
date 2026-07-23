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
#>

$ErrorActionPreference = 'Stop'

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

function Get-ArmPython {
    # Prefer the py launcher, then any python on PATH; return the exe path only if it is ARM64 and >= min version.
    $candidates = @()
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $p = (& py "-$MinPythonMajor.$MinPythonMinor" -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p) { $candidates += $p.Trim() }
        $p2 = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $p2) { $candidates += $p2.Trim() }
    }
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }

    foreach ($exe in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path $exe)) { continue }
        $machine = (& $exe -c "import platform; print(platform.machine())" 2>$null)
        $ver     = (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
        if ($machine -match 'ARM64' -and $ver) {
            $mj,$mn = $ver.Split('.')
            if ([int]$mj -gt $MinPythonMajor -or ([int]$mj -eq $MinPythonMajor -and [int]$mn -ge $MinPythonMinor)) {
                return $exe
            }
        }
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
    # Refresh PATH for current session
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path','User')
    $pythonExe = Get-ArmPython
    if (-not $pythonExe) { throw "ARM64 Python still not detected after install. Open a new shell and re-run." }
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
if (Test-Path (Join-Path $VenvDir 'Scripts\Activate.ps1')) {
    Write-Ok "venv already exists, reusing it"
} else {
    & $pythonExe -m venv $VenvDir
    Write-Ok "venv created"
}

Write-Step "Activating venv and installing geniex from PyPI"
& (Join-Path $VenvDir 'Scripts\Activate.ps1')
python -m pip install --upgrade pip
pip install -U geniex

# --- 5. Verify -------------------------------------------------------------
Write-Step "Verifying geniex install"
python -c "import geniex; print('geniex version:', geniex.version())"

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host " GenieX environment ready." -ForegroundColor Green
Write-Host " To use it in a new shell:" -ForegroundColor Green
Write-Host "     .\geniex-env\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green
