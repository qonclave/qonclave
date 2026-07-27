# setup.ps1
# One-time setup: install all dependencies for the face identification pipeline.
# Run this once after cloning the repo, then use run.ps1 for every identify/compare/benchmark.
#
# Usage:
#   .\setup.ps1           # CPU mode setup
#   .\setup.ps1 -Npu      # CPU + NPU setup (exports AI Hub models, prompts for token)
#   .\setup.ps1 -Npu -Token YOUR_TOKEN

param(
    [switch]$Npu,
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$arch = $env:PROCESSOR_ARCHITECTURE   # ARM64 on WoS, AMD64 on x86

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan  }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Face ID Pipeline — Setup" -ForegroundColor Green
Write-Host "Platform: $arch" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: opencv (platform-aware) ──────────────────────────────────────────

Info "Installing opencv..."
if ($arch -eq "ARM64") {
    $wheel = Get-ChildItem "$ScriptDir\wheels" -Filter "opencv_python_headless-*-win_arm64.whl" |
             Sort-Object Name -Descending | Select-Object -First 1
    if (-not $wheel) {
        Write-Host ""
        Write-Host "  No ARM64 opencv wheel found in wheels\." -ForegroundColor Yellow
        Write-Host "  Run build_opencv_arm64.ps1 first to build it, then re-run setup.ps1." -ForegroundColor Yellow
        Fail "Missing ARM64 opencv wheel."
    }
    pip install $wheel.FullName --quiet
    Ok "opencv installed from local wheel: $($wheel.Name)"
} else {
    pip install opencv-python-headless --quiet
    Ok "opencv-python-headless installed"
}

# ── Step 2: onnxruntime-qnn (ARM64 only, needed for NPU) ─────────────────────

if ($arch -eq "ARM64") {
    Info "Installing onnxruntime-qnn (for NPU support)..."
    pip install onnxruntime-qnn --quiet
    Ok "onnxruntime-qnn installed"
}

# ── Step 3: mediapipe + qai-hub-models ───────────────────────────────────────

Info "Installing mediapipe..."
pip install mediapipe --no-deps --quiet
Ok "mediapipe installed"

Info "Installing qai-hub-models[cavaface] + pillow + numpy..."
pip install "qai-hub-models[cavaface]" pillow numpy --quiet -c "$ScriptDir\constraints.txt"
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
Ok "qai-hub-models installed"

# ── Step 4: Download MediaPipe CPU model ─────────────────────────────────────

$mpModel = "$ScriptDir\face_detector.tflite"
if (-not (Test-Path $mpModel)) {
    Info "Downloading MediaPipe face detector model (~228KB)..."
    Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" `
        -OutFile $mpModel -UseBasicParsing
    Ok "face_detector.tflite downloaded"
} else {
    Ok "face_detector.tflite already present"
}

# ── Step 5: (Optional) NPU model export ──────────────────────────────────────

if ($Npu) {
    Write-Host ""
    Info "Running NPU setup (exports MediaPipeFace + CavaFace via AI Hub)..."
    if ($Token) {
        & "$ScriptDir\setup_npu.ps1" -Token $Token
    } else {
        & "$ScriptDir\setup_npu.ps1"
    }
    if ($LASTEXITCODE -ne 0) { Fail "NPU setup failed." }
}

# ── Done ─────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host " Add face photos to known_faces\ then run:" -ForegroundColor Cyan
if ($Npu) {
    Write-Host "   .\run.ps1 identify -Image unknown.jpg -Npu" -ForegroundColor White
} else {
    Write-Host "   .\run.ps1 identify -Image unknown.jpg" -ForegroundColor White
}
Write-Host "================================================================" -ForegroundColor Green
