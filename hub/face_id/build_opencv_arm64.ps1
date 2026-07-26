# build_opencv_arm64.ps1
# Builds opencv-python from source for Windows ARM64 (Snapdragon X / WoS)
#
# Fully self-bootstrapping - installs everything it needs:
#   - winget       (via MSIX bootstrap if missing - needs elevation once)
#   - Git          (via winget)
#   - CMake        (via winget)
#   - Python 3.12 ARM64 (downloaded from python.org)
#   - VS 2022 Build Tools + ARM64 MSVC toolchain (downloaded from Microsoft)
#
# Usage:
#   .\build_opencv_arm64.ps1
#   .\build_opencv_arm64.ps1 -Version 4.10.0 -Jobs 8

param(
    [string]$Version  = "4.10.0",
    [int]   $Jobs     = 0,
    [string]$BuildDir = "$env:USERPROFILE\opencv_arm64_build",
    [switch]$SkipClone,
    [switch]$WheelOnly
)

$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan    }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green   }
function Warn { param($m) Write-Host "[WARN]  $m" -ForegroundColor Yellow  }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

function RefreshPath {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
}

function Install-ViaWinget {
    param([string]$Id, [string]$Label)
    Info "Installing $Label via winget..."
    winget install --id $Id --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fail "Failed to install $Label" }
    RefreshPath
    Ok "$Label installed"
}

function Bootstrap-Winget {
    Info "winget not found - bootstrapping App Installer..."
    $vcPath   = "$env:TEMP\VCLibs.appx"
    $msixPath = "$env:TEMP\AppInstaller.msixbundle"
    Info "Downloading VC++ Runtime for winget..."
    Invoke-WebRequest -Uri "https://aka.ms/Microsoft.VCLibs.arm64.14.00.Desktop.appx" -OutFile $vcPath -UseBasicParsing
    Add-AppxPackage -Path $vcPath -ErrorAction SilentlyContinue
    Info "Downloading App Installer (winget)..."
    Invoke-WebRequest -Uri "https://aka.ms/getwinget" -OutFile $msixPath -UseBasicParsing
    Info "Installing App Installer (may require elevation)..."
    Add-AppxPackage -Path $msixPath
    RefreshPath
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget still not available. Please reboot and re-run this script."
    }
    Ok "winget installed"
}

# ── Install prerequisites ─────────────────────────────────────────────────────

Info "Checking and installing prerequisites..."

# winget
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Bootstrap-Winget
} else { Ok "winget found" }

# Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Install-ViaWinget "Git.Git" "Git"
} else { Ok "Git found" }

# CMake
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    Install-ViaWinget "Kitware.CMake" "CMake"
} else { Ok "CMake found" }

# Visual Studio 2022 Build Tools + ARM64 toolchain
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Info "Visual Studio not found - installing VS 2022 Build Tools..."
    Info "This may take 10-15 minutes..."
    $vsBootstrap = "$env:TEMP\vs_buildtools.exe"
    Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vs_buildtools.exe" -OutFile $vsBootstrap -UseBasicParsing
    Start-Process -FilePath $vsBootstrap -Wait -NoNewWindow -ArgumentList @(
        "--quiet", "--wait", "--norestart",
        "--add", "Microsoft.VisualStudio.Workload.VCTools",
        "--add", "Microsoft.VisualStudio.Component.VC.Tools.ARM64",
        "--add", "Microsoft.VisualStudio.Component.Windows11SDK.22621"
    )
    Ok "Visual Studio 2022 Build Tools installed"
} else { Ok "Visual Studio installer found" }

# Python ARM64
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Info "Python not found - downloading Python 3.12 ARM64..."
    $pyInstaller = "$env:TEMP\python_arm64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.8/python-3.12.8-arm64.exe" -OutFile $pyInstaller -UseBasicParsing
    Start-Process -FilePath $pyInstaller -Wait -NoNewWindow -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1"
    RefreshPath
    Ok "Python 3.12 ARM64 installed"
}

$pyArch = python -c "import platform; print(platform.machine())"
if ($pyArch -ne "ARM64") {
    Fail "Python is $pyArch not ARM64. Download the ARM64 installer from https://python.org"
}
$pyVer = python -c "import sys; print(str(sys.version_info.major) + '.' + str(sys.version_info.minor))"
$pyTag = python -c "import sys; print(str(sys.version_info.major) + str(sys.version_info.minor))"
Ok "Python $pyVer ARM64"

# Check ARM64 MSVC toolchain
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vsPath  = & $vswhere -latest -property installationPath 2>$null
if (-not $vsPath) { Fail "No Visual Studio installation found." }
Ok "Visual Studio: $vsPath"

$msvcDir  = Get-ChildItem "$vsPath\VC\Tools\MSVC" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
$arm64Cl  = if ($msvcDir) { "$($msvcDir.FullName)\bin\HostX64\arm64\cl.exe" } else { "" }
if (-not $arm64Cl -or -not (Test-Path $arm64Cl)) {
    Fail "ARM64 MSVC toolchain not found. In VS Installer add: MSVC ARM64 build tools."
}
Ok "ARM64 cl.exe found"

# ── Python build deps ─────────────────────────────────────────────────────────

Info "Installing Python build dependencies..."
pip install --quiet numpy scikit-build setuptools wheel cmake
Ok "Python deps ready"

# ── Clone OpenCV ──────────────────────────────────────────────────────────────

if (-not $WheelOnly) {
    New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
    $opencvSrc   = "$BuildDir\opencv"
    $opencvBuild = "$BuildDir\build"

    if (-not $SkipClone) {
        Info "Cloning opencv $Version..."
        if (Test-Path $opencvSrc) {
            git -C $opencvSrc fetch --tags
        } else {
            git clone --depth 1 --branch $Version https://github.com/opencv/opencv.git $opencvSrc
        }
        Ok "Cloned opencv $Version"
    } else {
        Info "Skipping clone (-SkipClone set)"
    }

    # ── CMake configure ───────────────────────────────────────────────────────

    Info "Configuring CMake for ARM64..."
    New-Item -ItemType Directory -Force -Path $opencvBuild | Out-Null

    $pyExe     = (Get-Command python).Source
    $pyInclude = python -c "import sysconfig; print(sysconfig.get_path('include'))"

    $cmakeArgs = @(
        $opencvSrc,
        "-G", "Visual Studio 17 2022",
        "-A", "ARM64",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_opencv_python3=ON",
        "-DBUILD_opencv_python2=OFF",
        "-DPYTHON3_EXECUTABLE=$pyExe",
        "-DPYTHON3_INCLUDE_DIR=$pyInclude",
        "-DBUILD_opencv_dnn=OFF",
        "-DENABLE_NEON=OFF",
        "-DWITH_FFMPEG=OFF",
        "-DWITH_MSMF=ON",
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
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "CMake configuration failed" }
    Ok "CMake configured"

    # ── Build ─────────────────────────────────────────────────────────────────

    if ($Jobs -eq 0) { $Jobs = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors }
    Info "Building with $Jobs parallel jobs (20-40 minutes)..."
    cmake --build . --config Release --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Build failed" }
    Ok "Build complete"

    cmake --install . --config Release
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Install failed" }
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
$env:OPENCV_PYTHON_BUILD_PATH = $opencvBuild
python setup.py bdist_wheel --dist-dir $wheelOut -- -G "Visual Studio 17 2022" -A ARM64

if ($LASTEXITCODE -ne 0) {
    Warn "setup.py wheel failed - trying manual .pyd packaging..."
    $pyd = Get-ChildItem "$opencvBuild\lib\python3\Release" -Filter "cv2*.pyd" -ErrorAction SilentlyContinue |
           Select-Object -First 1
    if (-not $pyd) { Pop-Location; Fail "cv2.pyd not found in build output" }

    $wheelName = "opencv_python_headless-$Version-cp$pyTag-cp$pyTag-win_arm64.whl"
    $wheelPath = "$wheelOut\$wheelName"
    $tmp       = "$env:TEMP\cv2wheel"
    $distInfo  = "$tmp\opencv_python_headless-$Version.dist-info"

    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $distInfo | Out-Null
    Copy-Item $pyd.FullName "$tmp\cv2.pyd"

    Set-Content "$distInfo\WHEEL" @"
Wheel-Version: 1.0
Generator: build_opencv_arm64.ps1
Root-Is-Purelib: false
Tag: cp$pyTag-cp$pyTag-win_arm64
"@
    Set-Content "$distInfo\METADATA" @"
Metadata-Version: 2.1
Name: opencv-python-headless
Version: $Version
"@
    Compress-Archive -Path "$tmp\*" -DestinationPath "$wheelPath.zip" -Force
    Rename-Item "$wheelPath.zip" $wheelPath
    Ok "Wheel packaged: $wheelPath"
}
Pop-Location

# ── Install wheel ─────────────────────────────────────────────────────────────

$wheel = Get-ChildItem $wheelOut -Filter "*.whl" |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) { Fail "No wheel found in $wheelOut" }

Info "Installing $($wheel.Name)..."
pip install $wheel.FullName --force-reinstall
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }

Ok "opencv-python ARM64 installed!"
Info "Wheel saved at: $($wheel.FullName)"
Info ""
Info "Test with:"
Info "  python -c `"import cv2; print(cv2.__version__)`""
