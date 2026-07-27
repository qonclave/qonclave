# Face identification pipeline — Windows (x86 and ARM64/WoS)
# Usage:
#   .\run.ps1 compare   -Image1 a.jpg -Image2 b.jpg
#   .\run.ps1 identify  -Image unknown.jpg
#   .\run.ps1 benchmark -Image photo.jpg
#   .\run.ps1 identify  -Image unknown.jpg -Npu   <- NPU mode (Snapdragon X)

param(
    [string]$Mode   = "help",
    [string]$Image1 = "",
    [string]$Image2 = "",
    [string]$Image  = "",
    [switch]$Npu                  # use NPU via QNNExecutionProvider (ARM64 only)
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$arch = $env:PROCESSOR_ARCHITECTURE   # ARM64 on WoS, AMD64 on x86

# ── Install dependencies ──────────────────────────────────────────────────────
Write-Host "Checking dependencies..." -ForegroundColor Cyan

if ($arch -eq "ARM64") {
    # Install pre-built local wheel (no pip wheel for Windows ARM64)
    $wheel = Get-ChildItem "$ScriptDir\wheels" -Filter "opencv_python_headless-*-win_arm64.whl" |
             Sort-Object Name -Descending | Select-Object -First 1
    if (-not $wheel) {
        Write-Host "[ERROR] No ARM64 opencv wheel in wheels\. Run build_opencv_arm64.ps1 first." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Installing opencv from local wheel: $($wheel.Name)" -ForegroundColor Cyan
    pip install $wheel.FullName --quiet

    # onnxruntime-qnn: has native win_arm64 wheel — needed for NPU path
    pip install onnxruntime-qnn --quiet
}

# mediapipe --no-deps: skips opencv-contrib (not needed for mp.tasks API)
pip install mediapipe --no-deps --quiet
# constraints.txt pins opencv-python-headless so qai-hub-models dep is satisfied
pip install "qai-hub-models[cavaface]" pillow numpy --quiet -c "$ScriptDir\constraints.txt"
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed." -ForegroundColor Red
    exit 1
}
Write-Host "Dependencies ready." -ForegroundColor Green

# ── Build extra args ──────────────────────────────────────────────────────────
$NpuFlag = if ($Npu) { "--npu" } else { "" }

if ($Npu -and $arch -ne "ARM64") {
    Write-Host "[WARN] --npu flag is only supported on ARM64 (Snapdragon X). Running CPU mode." -ForegroundColor Yellow
    $NpuFlag = ""
}

# ── Parse mode ────────────────────────────────────────────────────────────────
switch ($Mode.ToLower()) {
    "compare" {
        if (-not $Image1 -or -not $Image2) {
            Write-Host "Usage: .\run.ps1 compare -Image1 <img1> -Image2 <img2> [-Npu]" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py $NpuFlag compare $Image1 $Image2
    }
    "identify" {
        $img = if ($Image) { $Image } else { $Image1 }
        if (-not $img) {
            Write-Host "Usage: .\run.ps1 identify -Image <img> [-Npu]" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py $NpuFlag identify $img --db .\known_faces\
    }
    "benchmark" {
        $img = if ($Image) { $Image } else { $Image1 }
        if (-not $img) {
            Write-Host "Usage: .\run.ps1 benchmark -Image <img> [-Npu]" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py $NpuFlag benchmark $img --runs 20
    }
    default {
        Write-Host ""
        Write-Host "Face Identification Pipeline — MediaPipe + CavaFace" -ForegroundColor Green
        Write-Host "Platform: $arch" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "Usage:"
        Write-Host "  .\run.ps1 compare   -Image1 <img1> -Image2 <img2>  [-Npu]"
        Write-Host "  .\run.ps1 identify  -Image  <img>                  [-Npu]"
        Write-Host "  .\run.ps1 benchmark -Image  <img>                  [-Npu]"
        Write-Host ""
        Write-Host "  -Npu : Use Hexagon NPU (ARM64 only, needs models\CavaFace.onnx)"
        Write-Host "         See models\README.txt for export instructions."
        Write-Host ""
        Write-Host "known_faces\ folder:"
        Write-Host "  Put one reference image per person, named as the person's name."
        Write-Host "  Example: known_faces\mahesh_babu.jpg"
        Write-Host ""
    }
}
