# build_opencv_arm64.ps1
# Builds opencv-python from source for Windows ARM64 (Snapdragon X / WoS)
#
# Prerequisites (auto-checked):
#   - Python ARM64 installed
#   - Visual Studio 2022 with ARM64 build tools
#   - CMake
#   - Git
#
# Usage:
#   .\build_opencv_arm64.ps1
#   .\build_opencv_arm64.ps1 -Version 4.10.0 -Jobs 8

param(
    [string]$Version  = "4.10.0",
    [int]   $Jobs     = 0,           # 0 = auto (logical CPU count)
    [string]$BuildDir = "$env:USERPROFILE\opencv_arm64_build",
    [switch]$SkipClone,              # skip git clone if already done
    [switch]$WheelOnly               # skip cmake/build, just build wheel
)

$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Info  { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan    }
function Ok    { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green   }
function Warn  { param($m) Write-Host "[WARN]  $m" -ForegroundColor Yellow  }
function Fail  { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

function Require-Command {
    param($cmd, $hint)
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "$cmd not found. $hint"
    }
    Ok "$cmd found"
}

# ── Check prerequisites ───────────────────────────────────────────────────────

Info "Checking prerequisites..."

# Python — must be ARM64 native
Require-Command python "Install Python ARM64 from https://python.org (Windows ARM64 installer)"
$pyArch = python -c "import platform; print(platform.machine())"
if ($pyArch -ne "ARM64") {
    Fail "Python is $pyArch, not ARM64. Download the ARM64 installer from python.org"
}
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Ok "Python $pyVer ARM64"

# CMake
Require-Command cmake "Run: winget install Kitware.CMake"

# Git
Require-Command git "Run: winget install Git.Git"

# Visual Studio ARM64 toolchain
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Fail "Visual Studio not found. Install VS 2022 with 'Desktop development with C++' + ARM64 build tools."
}
$vsPath = & $vswhere -latest -property installationPath
if (-not $vsPath) { Fail "No Visual Studio installation found." }
Ok "Visual Studio: $vsPath"

# Check ARM64 toolchain exists
$arm64Cl = "$vsPath\VC\Tools\MSVC" | Get-ChildItem -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1 |
    ForEach-Object { "$($_.FullName)\bin\HostX64\arm64\cl.exe" }
if (-not $arm64Cl -or -not (Test-Path $arm64Cl)) {
    Fail "ARM64 MSVC toolchain not found at $arm64Cl`nIn VS Installer, add: Individual components → MSVC ARM64 build tools"
}
Ok "ARM64 cl.exe: $arm64Cl"

# ── Install Python build deps ─────────────────────────────────────────────────

Info "Installing Python build dependencies..."
pip install --quiet numpy scikit-build setuptools wheel cmake
Ok "Python deps installed"

# ── Clone OpenCV ──────────────────────────────────────────────────────────────

if (-not $WheelOnly) {
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    $opencvSrc    = "$BuildDir\opencv"
    $opencvBuild  = "$BuildDir\build"

    if (-not $SkipClone) {
        Info "Cloning opencv $Version..."
        if (Test-Path $opencvSrc) {
            Info "Source dir exists, pulling latest..."
            git -C $opencvSrc fetch --tags
        } else {
            git clone --depth 1 --branch $Version https://github.com/opencv/opencv.git $opencvSrc
        }
        Ok "Cloned opencv $Version"
    } else {
        Info "Skipping clone (--SkipClone set)"
    }

    # ── CMake configure ───────────────────────────────────────────────────────

    Info "Configuring CMake for ARM64..."
    New-Item -ItemType Directory -Force -Path $opencvBuild | Out-Null

    $pyExe     = (Get-Command python).Source
    $pyInclude = python -c "import sysconfig; print(sysconfig.get_path('include'))"
    $pyLib     = python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR') or sysconfig.get_path('stdlib'))"

    $cmakeArgs = @(
        $opencvSrc,
        "-G", "Visual Studio 17 2022",
        "-A", "ARM64",
        "-DCMAKE_BUILD_TYPE=Release",

        # Python binding
        "-DBUILD_opencv_python3=ON",
        "-DBUILD_opencv_python2=OFF",
        "-DPYTHON3_EXECUTABLE=$pyExe",
        "-DPYTHON3_INCLUDE_DIR=$pyInclude",

        # Disable problematic modules for ARM64
        "-DBUILD_opencv_dnn=OFF",       # MSVC ARM64 FP16 issues
        "-DENABLE_NEON=OFF",            # MSVC NEON intrinsics issues
        "-DWITH_FFMPEG=OFF",            # No ARM64 FFMPEG DLLs
        "-DWITH_MSMF=ON",               # Use Media Foundation instead

        # Skip unnecessary components
        "-DBUILD_TESTS=OFF",
        "-DBUILD_PERF_TESTS=OFF",
        "-DBUILD_EXAMPLES=OFF",
        "-DBUILD_DOCS=OFF",
        "-DWITH_CUDA=OFF",
        "-DWITH_OPENCL=OFF",

        "-DCMAKE_INSTALL_PREFIX=$BuildDir\install"
    )

    Push-Location $opencvBuild
    cmake @cmakeArgs
    if ($LASTEXITCODE -ne 0) { Fail "CMake configuration failed" }
    Ok "CMake configured"

    # ── Build ─────────────────────────────────────────────────────────────────

    if ($Jobs -eq 0) { $Jobs = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors }
    Info "Building with $Jobs parallel jobs (this takes 20-40 minutes)..."

    cmake --build . --config Release --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { Fail "Build failed" }
    Ok "Build complete"

    cmake --install . --config Release
    if ($LASTEXITCODE -ne 0) { Fail "Install failed" }
    Ok "Installed to $BuildDir\install"

    Pop-Location
}

# ── Generate wheel ────────────────────────────────────────────────────────────

Info "Generating pip wheel..."

$opencvSrc   = "$BuildDir\opencv"
$opencvBuild = "$BuildDir\build"
$wheelOut    = "$BuildDir\wheels"
New-Item -ItemType Directory -Force -Path $wheelOut | Out-Null

Push-Location "$opencvSrc\modules\python\package"

# Point setup.py at our build
$env:OPENCV_PYTHON_BUILD_PATH = $opencvBuild
python setup.py bdist_wheel `
    --dist-dir $wheelOut `
    -- `
    -G "Visual Studio 17 2022" -A ARM64

if ($LASTEXITCODE -ne 0) {
    # fallback: grab the .pyd from the build tree and package manually
    Warn "setup.py wheel failed, trying direct .pyd packaging..."

    $pyd = Get-ChildItem "$opencvBuild\lib\python3\Release" -Filter "cv2*.pyd" | Select-Object -First 1
    if (-not $pyd) { Fail "cv2 .pyd not found in build output" }

    $wheelName = "opencv_python_headless-$Version-cp$($pyVer.Replace('.',''))-cp$($pyVer.Replace('.',''))-win_arm64.whl"
    $wheelPath = "$wheelOut\$wheelName"

    # Minimal wheel: just the .pyd + WHEEL/METADATA
    $tmp = "$env:TEMP\cv2wheel"
    New-Item -ItemType Directory -Force -Path "$tmp\opencv_python_headless-$Version.dist-info" | Out-Null
    Copy-Item $pyd.FullName "$tmp\cv2.pyd"

    @"
Wheel-Version: 1.0
Generator: build_opencv_arm64.ps1
Root-Is-Purelib: false
Tag: cp$($pyVer.Replace('.',''))-cp$($pyVer.Replace('.',''))-win_arm64
"@ | Set-Content "$tmp\opencv_python_headless-$Version.dist-info\WHEEL"

    @"
Metadata-Version: 2.1
Name: opencv-python-headless
Version: $Version
"@ | Set-Content "$tmp\opencv_python_headless-$Version.dist-info\METADATA"

    Compress-Archive -Path "$tmp\*" -DestinationPath "$wheelPath.zip" -Force
    Rename-Item "$wheelPath.zip" $wheelPath
    Ok "Wheel packaged manually: $wheelPath"
}

Pop-Location

# ── Install wheel ─────────────────────────────────────────────────────────────

$wheel = Get-ChildItem $wheelOut -Filter "*.whl" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) { Fail "No wheel found in $wheelOut" }

Info "Installing $($wheel.Name)..."
pip install $wheel.FullName --force-reinstall
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

Ok "opencv-python installed for Windows ARM64!"
Info "Wheel saved at: $($wheel.FullName)"
Info ""
Info "Test with:"
Info "  python -c `"import cv2; print(cv2.__version__, cv2.getBuildInformation())`""
