# setup_npu.ps1
# Exports MediaPipe Face Detector + CavaFace ONNX models via Qualcomm AI Hub
# and copies them to hub/face_id/models/ for full NPU inference.
#
# Both models run on Hexagon NPU (Snapdragon X Elite):
#   MediaPipeFace  ~ 0.7ms  (face detection)
#   CavaFace       ~ 4.3ms  (face embedding)
#   Total pipeline ~ 5ms    (vs ~265ms CPU)
#
# AI Hub output structure (discovered empirically):
#   <name>.onnx.zip
#     └── job_<jobid>_optimized_onnx/
#           ├── model.onnx   (graph)
#           └── model.data   (weights, referenced by model.onnx)
#
# Usage:
#   .\setup_npu.ps1                        # prompts for token
#   .\setup_npu.ps1 -Token YOUR_TOKEN      # pass token directly

param(
    [string]$Token  = "",
    [string]$Device = "Snapdragon X Elite CRD"
)

$ErrorActionPreference = "Stop"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ModelsDir   = Join-Path $ScriptDir "models"
$DownloadDir = Join-Path $env:TEMP "qonclave_npu_export"

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan   }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green  }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

function Extract-And-Copy {
    param([string]$ZipPath, [string]$DestName)

    $unzipDir = "$DownloadDir\extracted_$DestName"
    Expand-Archive -Path $ZipPath -DestinationPath $unzipDir -Force

    $onnxFile = Get-ChildItem $unzipDir -Recurse -Filter "model.onnx" | Select-Object -First 1
    $dataFile = Get-ChildItem $unzipDir -Recurse -Filter "model.data" | Select-Object -First 1

    if (-not $onnxFile) { Fail "model.onnx not found in $ZipPath" }

    Copy-Item $onnxFile.FullName "$ModelsDir\$DestName.onnx" -Force
    Ok "Copied: $DestName.onnx  ($([math]::Round($onnxFile.Length/1KB)) KB)"

    if ($dataFile) {
        Copy-Item $dataFile.FullName "$ModelsDir\$DestName.data" -Force
        Ok "Copied: $DestName.data  ($([math]::Round($dataFile.Length/1MB)) MB)"
    }
}

function Export-Model {
    param([string]$ModelName, [string]$DestName)

    Info "Exporting $ModelName for '$Device' (onnx)..."
    New-Item -ItemType Directory -Force -Path "$DownloadDir\$DestName" | Out-Null

    Push-Location "$DownloadDir\$DestName"
    qai-hub-models export $ModelName `
        --target-runtime onnx `
        --device $Device `
        --output-dir "$DownloadDir\$DestName\export_assets"
    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Export of $ModelName failed." }
    Pop-Location
    Ok "$ModelName export complete"

    $zipFile = Get-ChildItem "$DownloadDir\$DestName\export_assets" -Recurse -Filter "*.onnx.zip" |
               Select-Object -First 1
    if (-not $zipFile) { Fail "No .onnx.zip found for $ModelName" }

    Extract-And-Copy -ZipPath $zipFile.FullName -DestName $DestName
}

# ── Step 1: Get token ─────────────────────────────────────────────────────────

if (-not $Token) {
    Write-Host ""
    Write-Host "Qualcomm AI Hub token required." -ForegroundColor Cyan
    Write-Host "Sign up free at  : https://workbench.aihub.qualcomm.com" -ForegroundColor Cyan
    Write-Host "Get your token   : Account -> Settings -> API Token" -ForegroundColor Cyan
    Write-Host ""
    $Token = Read-Host "Enter your AI Hub API token"
    if (-not $Token) { Fail "No token provided." }
}

# ── Step 2: Install and configure ────────────────────────────────────────────

Info "Installing qai-hub-models..."
pip install --quiet qai-hub "qai-hub-models[mediapipe_face,cavaface]" -c "$ScriptDir\constraints.txt"
Ok "Packages ready"

Info "Configuring AI Hub..."
qai-hub configure --api_token $Token
if ($LASTEXITCODE -ne 0) { Fail "AI Hub configuration failed. Check your token." }
Ok "AI Hub configured"

# ── Step 3: Create output dirs ────────────────────────────────────────────────

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $ModelsDir   | Out-Null

# ── Step 4: Export both models ────────────────────────────────────────────────

Write-Host ""
Write-Host "Exporting 2 models — this takes ~5-10 minutes total..." -ForegroundColor Cyan
Write-Host ""

Export-Model -ModelName "mediapipe_face" -DestName "MediaPipeFace"
Export-Model -ModelName "cavaface"       -DestName "CavaFace"

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Full NPU pipeline ready!" -ForegroundColor Green
Write-Host ""
Write-Host " Models in: $ModelsDir" -ForegroundColor Cyan
Write-Host "   MediaPipeFace.onnx  (face detector ~0.7ms NPU)" -ForegroundColor White
Write-Host "   CavaFace.onnx       (face embedding ~4.3ms NPU)" -ForegroundColor White
Write-Host "   CavaFace.data       (weights ~250MB)" -ForegroundColor White
Write-Host ""
Write-Host " Run with NPU:" -ForegroundColor Cyan
Write-Host "   .\run.ps1 identify  -Image unknown.jpg -Npu" -ForegroundColor White
Write-Host "   .\run.ps1 benchmark -Image photo.jpg   -Npu" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
