# Face identification pipeline — Windows (x86 and ARM64/WoS)
# Usage:
#   .\run.ps1 compare  -Image1 a.jpg -Image2 b.jpg
#   .\run.ps1 identify -Image unknown.jpg
#   .\run.ps1 benchmark -Image photo.jpg

param(
    [string]$Mode   = "help",
    [string]$Image1 = "",
    [string]$Image2 = "",
    [string]$Image  = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ── Install opencv: platform-aware ───────────────────────────────────────────
Write-Host "Checking dependencies..." -ForegroundColor Cyan

$arch = $env:PROCESSOR_ARCHITECTURE   # ARM64 on WoS, AMD64 on x86

if ($arch -eq "ARM64") {
    # No pip wheel exists for Windows ARM64 — install from pre-built local wheel
    $wheel = Get-ChildItem "$ScriptDir\wheels" -Filter "opencv_python_headless-*-win_arm64.whl" |
             Sort-Object Name -Descending | Select-Object -First 1
    if (-not $wheel) {
        Write-Host "[ERROR] No ARM64 opencv wheel found in wheels\ folder." -ForegroundColor Red
        Write-Host "        Run build_opencv_arm64.ps1 first to build it." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Installing opencv from local wheel: $($wheel.Name)" -ForegroundColor Cyan
    pip install $wheel.FullName --quiet
} else {
    # Windows x86/x64 — standard pip wheel available
    pip install opencv-python-headless --quiet
}

# mediapipe --no-deps: skips opencv-contrib (not needed for mp.tasks API)
pip install mediapipe --no-deps --quiet
# qai-hub-models AFTER opencv so pip sees cv2 already satisfied and skips opencv-contrib
pip install "qai-hub-models[cavaface]" pillow numpy --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Make sure Python is in PATH." -ForegroundColor Red
    exit 1
}
Write-Host "Dependencies ready." -ForegroundColor Green

# ── Parse mode ────────────────────────────────────────────────────────────────
switch ($Mode.ToLower()) {
    "compare" {
        if (-not $Image1 -or -not $Image2) {
            Write-Host "Usage: .\run.ps1 compare -Image1 <img1> -Image2 <img2>" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py --image1 $Image1 --image2 $Image2
    }
    "identify" {
        $img = if ($Image) { $Image } else { $Image1 }
        if (-not $img) {
            Write-Host "Usage: .\run.ps1 identify -Image <unknown_image>" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py --identify $img --db .\known_faces\
    }
    "benchmark" {
        $img = if ($Image) { $Image } else { $Image1 }
        if (-not $img) {
            Write-Host "Usage: .\run.ps1 benchmark -Image <image>" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py benchmark $img --runs 20
    }
    default {
        Write-Host ""
        Write-Host "Face Identification Pipeline — MediaPipe + CavaFace" -ForegroundColor Green
        Write-Host ""
        Write-Host "Usage:"
        Write-Host "  .\run.ps1 compare   -Image1 <img1> -Image2 <img2>   Compare two face images"
        Write-Host "  .\run.ps1 identify  -Image  <img>                   Identify against known_faces\"
        Write-Host "  .\run.ps1 benchmark -Image  <img>                   Benchmark end-to-end speed"
        Write-Host ""
        Write-Host "known_faces\ folder:"
        Write-Host "  Put one reference image per person, named as the person's name."
        Write-Host "  Example: known_faces\john_doe.jpg"
        Write-Host ""
        Write-Host "Platform: $arch"
        Write-Host ""
    }
}
