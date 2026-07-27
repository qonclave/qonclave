# setup_npu.ps1
# Exports CavaFace ONNX model via Qualcomm AI Hub and copies it to
# hub/face_id/models/ ready for NPU inference.
#
# AI Hub output structure (discovered empirically):
#   <name>.onnx.zip
#     └── job_<jobid>_optimized_onnx/
#           ├── model.onnx   (~264 KB  — graph)
#           └── model.data   (~250 MB  — weights, referenced by model.onnx)
#
# Usage:
#   .\setup_npu.ps1                     # prompts for token interactively
#   .\setup_npu.ps1 -Token YOUR_TOKEN   # pass token directly
#   .\setup_npu.ps1 -JobId jpymz7w7p    # reuse an already-completed job

param(
    [string]$Token  = "",
    [string]$Device = "Snapdragon X Elite CRD",
    [string]$JobId  = ""   # reuse existing completed job (skip compile)
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot   = Split-Path $ScriptDir -Parent
$ModelsDir  = Join-Path $ScriptDir "models"
$DownloadDir = Join-Path $env:TEMP "qonclave_npu_export"

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan   }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green  }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

# ── Step 1: Get AI Hub token ──────────────────────────────────────────────────

if (-not $JobId) {
    if (-not $Token) {
        Write-Host ""
        Write-Host "Qualcomm AI Hub token required." -ForegroundColor Cyan
        Write-Host "Sign up free at  : https://workbench.aihub.qualcomm.com" -ForegroundColor Cyan
        Write-Host "Get your token   : Account -> Settings -> API Token" -ForegroundColor Cyan
        Write-Host ""
        $Token = Read-Host "Enter your AI Hub API token"
        if (-not $Token) { Fail "No token provided." }
    }

    # ── Step 2: Install and configure ────────────────────────────────────────

    Info "Installing qai-hub and qai-hub-models..."
    pip install --quiet qai-hub "qai-hub-models[cavaface]" -c "$ScriptDir\constraints.txt"
    Ok "Packages ready"

    Info "Configuring AI Hub..."
    qai-hub configure --api_token $Token
    if ($LASTEXITCODE -ne 0) { Fail "AI Hub configuration failed. Check your token." }
    Ok "AI Hub configured"

    # ── Step 3: Export CavaFace ONNX via AI Hub ───────────────────────────────

    Info "Exporting CavaFace for '$Device' (runtime: onnx)..."
    Info "This submits a compile job to AI Hub cloud (~2-5 minutes)..."

    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    Push-Location $DownloadDir

    qai-hub-models export cavaface `
        --target-runtime onnx `
        --device $Device `
        --output-dir "$DownloadDir\export_assets"

    if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Export failed. Check your AI Hub account." }
    Pop-Location
    Ok "Export complete"

    # Find the zip
    $zipFile = Get-ChildItem "$DownloadDir\export_assets" -Recurse -Filter "*.onnx.zip" |
               Select-Object -First 1
    if (-not $zipFile) {
        Fail "No .onnx.zip found in $DownloadDir\export_assets"
    }

} else {
    # Reuse an existing job — download directly via qai-hub Python API
    Info "Reusing existing job: $JobId"

    if (-not $Token) {
        $Token = Read-Host "Enter your AI Hub API token (needed to download)"
    }
    pip install --quiet qai-hub -c "$ScriptDir\constraints.txt"
    qai-hub configure --api_token $Token

    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null

    python -c "
import sys, qai_hub as hub

try:
    job = hub.get_job('$JobId')
except Exception as e:
    print(f'ERROR: Could not find job $JobId — {e}', file=sys.stderr)
    sys.exit(1)

status = job.get_status().code
if status != 'SUCCESS':
    print(f'ERROR: Job $JobId is not complete (status: {status}). Wait for it to finish or run a new export.', file=sys.stderr)
    sys.exit(2)

target = job.get_target_model()
target.download(r'$DownloadDir\cavaface_npu')
print('Downloaded')
"
    if ($LASTEXITCODE -eq 1) { Fail "Job '$JobId' not found on AI Hub. Check the job ID." }
    if ($LASTEXITCODE -eq 2) { Fail "Job '$JobId' is not complete yet. Check status at https://workbench.aihub.qualcomm.com/jobs/$JobId/" }
    if ($LASTEXITCODE -ne 0) { Fail "Download failed (exit $LASTEXITCODE)." }

    $zipFile = Get-ChildItem $DownloadDir -Filter "*.onnx.zip" | Select-Object -First 1
    if (-not $zipFile) { Fail "Download failed — no .onnx.zip in $DownloadDir" }
}

# ── Step 4: Unzip ─────────────────────────────────────────────────────────────
# AI Hub zip structure:
#   *.onnx.zip
#     └── job_<id>_optimized_onnx/
#           ├── model.onnx
#           └── model.data

Info "Extracting $($zipFile.Name)..."
$unzipDir = "$DownloadDir\extracted"
Expand-Archive -Path $zipFile.FullName -DestinationPath $unzipDir -Force
Ok "Extracted"

$onnxFile = Get-ChildItem $unzipDir -Recurse -Filter "model.onnx" | Select-Object -First 1
$dataFile = Get-ChildItem $unzipDir -Recurse -Filter "model.data" | Select-Object -First 1

if (-not $onnxFile) { Fail "model.onnx not found inside zip" }
Info "Found: $($onnxFile.FullName)"
if ($dataFile) { Info "Found: $($dataFile.FullName)" }

# ── Step 5: Copy to hub/face_id/models/ ──────────────────────────────────────

Info "Copying model files to $ModelsDir ..."
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null

Copy-Item $onnxFile.FullName "$ModelsDir\CavaFace.onnx" -Force
Ok "Copied: CavaFace.onnx  ($([math]::Round($onnxFile.Length/1KB)) KB)"

if ($dataFile) {
    Copy-Item $dataFile.FullName "$ModelsDir\CavaFace.data" -Force
    Ok "Copied: CavaFace.data  ($([math]::Round($dataFile.Length/1MB)) MB)"
}

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " CavaFace NPU model ready!" -ForegroundColor Green
Write-Host ""
Write-Host " Run with NPU:" -ForegroundColor Cyan
Write-Host "   .\run.ps1 identify  -Image unknown.jpg -Npu" -ForegroundColor White
Write-Host "   .\run.ps1 benchmark -Image photo.jpg   -Npu" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
