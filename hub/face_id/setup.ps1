# setup.ps1
# One-time setup for the face identification pipeline.
# Run once after cloning the repo, then run face_pipeline.py directly.
#
# On ARM64 (Snapdragon X / WoS): NPU export runs automatically.
# On x86 (dev/test machine): CPU only, no NPU export needed.
#
# Usage:
#   .\setup.ps1                     # auto-detects platform, uses python from PATH
#   .\setup.ps1 -Token YOUR_TOKEN   # pass AI Hub token directly (ARM64 only)
#
# Reuse an already-completed AI Hub compile job instead of recompiling
# (ARM64 only - see setup_npu.ps1 and README for details):
#   .\setup.ps1 -Token YOUR_TOKEN -MediaPipeFaceJobId jXXXXXXXX -CavaFaceJobId jXXXXXXXX
#
# Target Python: hub/server.py imports hub/face_id/identity.py directly (see
# hub/README.md), so face-ID's dependencies must live in whatever environment
# actually runs the hub server - not necessarily this machine's system Python.
# Pass that interpreter with -PythonPath:
#   .\setup.ps1 -PythonPath C:\path\to\python.exe
# scripts/setup_geniex.ps1 calls this script automatically and passes its own
# geniex-env interpreter, so the normal hub flow needs no manual run at all.
# Left unset, this installs into whatever `python` resolves to on PATH, which
# is what you want for standalone face_id use (no hub server involved).

param(
    [string]$Token = "",
    [string]$MediaPipeFaceJobId = "",
    [string]$CavaFaceJobId = "",
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan  }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

# $env:PROCESSOR_ARCHITECTURE reflects the CALLING PROCESS's architecture, not
# the OS - if powershell.exe itself is running under x64 emulation (WOW64
# file-system redirection silently substitutes the x64 System32 binary when
# launched from an x64/emulated parent shell), this reports AMD64 even on
# real ARM64 hardware, causing a fresh clone's setup to install the wrong
# (x86/CPU-only) dependency branch entirely. The registry value is a plain
# read of the true native system architecture and isn't subject to that
# per-process redirection.
$arch   = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment').PROCESSOR_ARCHITECTURE
$isArm  = ($arch -eq "ARM64")

if ($PythonPath) {
    if (-not (Test-Path $PythonPath)) { Fail "Given -PythonPath does not exist: $PythonPath" }
    $python = $PythonPath
    Info "Installing into the given Python: $python"
} else {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCmd) { Fail "No -PythonPath given and no 'python' on PATH." }
    $python = $pythonCmd.Source
    Info "No -PythonPath given - using python from PATH: $python"
    Info "  (standalone face_id use. To make face-ID available inside the hub"
    Info "   server, run scripts\setup_geniex.ps1 instead - it calls this script"
    Info "   with the interpreter that actually runs hub/server.py.)"
}

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
& $python -m pip install matplotlib absl-py sounddevice
if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
Ok "mediapipe installed"

if ($isArm) {
    # On win_arm64 + cp313, PyPI has no onnxruntime wheel below 1.24.2, which
    # conflicts with every qai-hub-models[cavaface] release (pins onnxruntime<1.23).
    # PyPI also has no torch/torchvision wheels at all for win_arm64 (only
    # download.pytorch.org does), and no pandas<=2.2.3 wheel either (only pandas
    # 3.x ships one, which needs a full C++ toolchain to build from source
    # otherwise). So: pull torch/torchvision from the PyTorch index, use pandas
    # 3.x, install the rest of qai-hub-models' deps explicitly, then install
    # qai-hub-models itself with --no-deps so its unsatisfiable pins are never
    # evaluated (the already-installed onnxruntime-qnn keeps satisfying
    # onnxruntime at runtime).
    Info "Installing qai-hub-models[cavaface] dependencies (ARM64 workaround)..."
    & $python -m pip install `
        "huggingface_hub<2.0,>=0.34.0" `
        "numpy<=2.4.4" "onnx<=1.18.0,>=1.17" `
        "torch<=2.11.0,>=2.4" "torchvision<=0.26.0,>=0.19" `
        "typing-extensions<=4.15.0,>=4.12.2" "tqdm<=4.67.3,>=4.66" `
        "qai_hub>=0.51.0" "filelock<=3.29.0,>=3.16.1" `
        inputimeout "pydantic<=2.13.3,>=2" pydantic_yaml `
        "packaging<=26.2.0,>24.2" platformdirs "qai_hub_models_cli==0.58.0" `
        yacs gitpython pillow schema requests_toolbelt "httpx<=0.28.1,>=0.27" `
        gdown boto3 "boto3-stubs[s3]" numpydoc pandas `
        tabulate ipython scipy coverage `
        --extra-index-url https://download.pytorch.org/whl/cpu -c "$ScriptDir\constraints.txt"
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }

    Info "Installing qai-hub-models[cavaface] (--no-deps)..."
    & $python -m pip install "qai-hub-models[cavaface]==0.58.0" --no-deps
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
} else {
    Info "Installing qai-hub-models[cavaface] + pillow + numpy..."
    & $python -m pip install "qai-hub-models[cavaface]" pillow numpy -c "$ScriptDir\constraints.txt"
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}
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
    $npuArgs = @{ PythonPath = $python }
    if ($Token) { $npuArgs.Token = $Token }
    if ($MediaPipeFaceJobId) { $npuArgs.MediaPipeFaceJobId = $MediaPipeFaceJobId }
    if ($CavaFaceJobId) { $npuArgs.CavaFaceJobId = $CavaFaceJobId }
    & "$ScriptDir\setup_npu.ps1" @npuArgs
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
