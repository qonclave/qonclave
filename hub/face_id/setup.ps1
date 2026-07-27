# setup.ps1
# One-time setup for the face identification pipeline.
# Run once after cloning the repo, then run face_pipeline.py directly.
#
# On ARM64 (Snapdragon X / WoS): NPU export runs automatically.
# On x86 (dev/test machine): CPU only, no NPU export needed.
#
# Usage:
#   .\setup.ps1                     # auto-detects platform
#   .\setup.ps1 -Token YOUR_TOKEN   # pass AI Hub token directly (ARM64 only)

param(
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$arch   = $env:PROCESSOR_ARCHITECTURE
$isArm  = ($arch -eq "ARM64")
$python = (Get-Command python).Source

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan  }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "Face ID Pipeline - Setup" -ForegroundColor Green
Write-Host "Platform: $arch" -ForegroundColor Cyan
if ($isArm) {
    Write-Host "Mode    : ARM64 - NPU export will run automatically" -ForegroundColor Cyan
} else {
    Write-Host "Mode    : x86 - CPU only" -ForegroundColor Cyan
}
Write-Host ""

# Step 1: opencv (platform-aware)

Info "Installing opencv..."
if ($arch -eq "ARM64") {
    $wheel = Get-ChildItem "$ScriptDir\wheels" -Filter "opencv_python_headless-*-win_arm64.whl" |
             Sort-Object Name -Descending | Select-Object -First 1
    if (-not $wheel) {
        Write-Host "  No ARM64 opencv wheel found in wheels\." -ForegroundColor Yellow
        Write-Host "  Run build_opencv_arm64.ps1 first, then re-run setup.ps1." -ForegroundColor Yellow
        Fail "Missing ARM64 opencv wheel."
    }
    & $python -m pip install $wheel.FullName
    Ok "opencv installed from local wheel: $($wheel.Name)"
} else {
    & $python -m pip install opencv-python-headless
    Ok "opencv-python-headless installed"
}

# Step 2: onnxruntime-qnn (ARM64 only, for NPU)

if ($arch -eq "ARM64") {
    Info "Installing onnxruntime-qnn (for NPU support)..."
    & $python -m pip install onnxruntime-qnn
    Ok "onnxruntime-qnn installed"
}

# Step 3: mediapipe + qai-hub-models

Info "Installing mediapipe..."
& $python -m pip install mediapipe --no-deps
Ok "mediapipe installed"

Info "Installing qai-hub-models[cavaface] + pillow + numpy..."
& $python -m pip install "qai-hub-models[cavaface]" pillow numpy -c "$ScriptDir\constraints.txt"
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
Ok "qai-hub-models installed"

# Step 4: Download MediaPipe CPU model

$mpModel = "$ScriptDir\face_detector.tflite"
if (-not (Test-Path $mpModel)) {
    Info "Downloading MediaPipe face detector model (~228KB)..."
    Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite" `
        -OutFile $mpModel -UseBasicParsing
    Ok "face_detector.tflite downloaded"
} else {
    Ok "face_detector.tflite already present"
}

# Step 5: NPU model export (mandatory on ARM64, skipped on x86)

if ($isArm) {
    Write-Host ""
    Info "ARM64 detected - exporting NPU models (mandatory for real-time performance)..."
    if ($Token) {
        & "$ScriptDir\setup_npu.ps1" -Token $Token
    } else {
        & "$ScriptDir\setup_npu.ps1"
    }
    if ($LASTEXITCODE -ne 0) { Fail "NPU setup failed." }
} else {
    Write-Host ""
    Info "x86 machine - skipping NPU export (CPU mode only)"
    Info "To run on Snapdragon X with NPU, execute setup.ps1 on the ARM64 machine."
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host " Add face photos to known_faces\ then run:" -ForegroundColor Cyan
if ($isArm) {
    Write-Host "   python face_pipeline.py --npu identify unknown.jpg" -ForegroundColor White
} else {
    Write-Host "   python face_pipeline.py identify unknown.jpg" -ForegroundColor White
}
Write-Host "================================================================" -ForegroundColor Green
