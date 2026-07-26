# build_opencv_arm64.ps1
# Builds opencv-python from source for Windows ARM64 (Snapdragon X / WoS)
#
# Fully self-bootstrapping — installs everything it needs:
#   - winget       (via MSIX bootstrap if missing — needs elevation once)
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

function Install-Winget {
    param($id, $label)
    Info "Installing $label..."
    winget install --id $id --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Fail "Failed to install $label via winget" }
    # Refresh PATH for this session
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    Ok "$label installed"
}

function Install-Winget-Bootstrap {
    # winget is part of "App Installer" — download and install it via MSIX
    Info "winget not found — bootstrapping App Installer..."
    $msixUrl  = "https://aka.ms/getwinget"
    $msixPath = "$env:TEMP\AppInstaller.msixbundle"
    Info "Downloading App Installer..."
    Invoke-WebRequest -Uri $msixUrl -OutFile $msixPath -UseBasicParsing
    Info "Installing App Installer (requires elevation)..."
    Add-AppxPackage -Path $msixPath
    # Also install VC++ Runtime required by winget
    $vcUrl  = "https://aka.ms/Microsoft.VCLibs.arm64.14.00.Desktop.appx"
    $vcPath = "$env:TEMP\VCLibs.appx"
    Invoke-WebRequest -Uri $vcUrl -OutFile $vcPath -UseBasicParsing
    Add-AppxPackage -Path $vcPath
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Fail "winget still not available after bootstrap. Please reboot and re-run."
    }
    Ok "winget installed"
}

# ── Install prerequisites ─────────────────────────────────────────────────────

Info "Checking and installing prerequisites..."

# winget — bootstrap if missing (older Win10/Win11 builds may not have it)
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Install-Winget-Bootstrap
}
Ok "winget found"

# Git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Install-Winget "Git.Git" "Git"
} else { Ok "Git found" }

# CMake
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    Install-Winget "Kitware.CMake" "CMake"
} else { Ok "CMake found" }

# Visual Studio 2022 Build Tools with ARM64 workload
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Info "Visual Studio not found — installing VS 2022 Build Tools with ARM64 support..."
    Info "This may take 10-15 minutes..."
    $vsBootstrap = "$env:TEMP\vs_buildtools.exe"
    Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vs_buildtools.exe" -OutFile $vsBootstrap
    # Workloads:
    #   Microsoft.VisualStudio.Workload.VCTools   = C++ build tools
    #   Microsoft.VisualStudio.Component.VC.Tools.ARM64 = ARM64 MSVC toolchain
    Start-Process -FilePath $vsBootstrap -ArgumentList @(
        "--quiet", "--wait", "--norestart",
        "--add", "Microsoft.VisualStudio.Workload.VCTools",
        "--add", "Microsoft.VisualStudio.Component.VC.Tools.ARM64",
        "--add", "Microsoft.VisualStudio.Component.Windows11SDK.22621"
    ) -Wait -NoNewWindow
    Ok "Visual Studio 2022 Build Tools installed"
} else {
    Ok "Visual Studio installer found"
}

# Python — must be ARM64 native
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Info "Python not found — downloading ARM64 installer..."
    $pyInstaller = "$env:TEMP\python_arm64.exe"
    # Python 3.12 ARM64
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.8/python-3.12.8-arm64.exe" -OutFile $pyInstaller
    Start-Process -FilePath $pyInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait -NoNewWindow
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
    Ok "Python 3.12 ARM64 installed"
}

$pyArch = python -c "import platform; print(platform.machine())"
if ($pyArch -ne "ARM64") {
    Fail "Python is $pyArch not ARM64. Please install the ARM64 build from https://python.org"
}
$pyVer = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Ok "Python $pyVer ARM64"

# Visual Studio ARM64 toolchain check
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    Fail "Visual Studio installer still not found after install attempt. Please reboot and re-run."
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
