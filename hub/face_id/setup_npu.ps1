# setup_npu.ps1
# Exports CavaFace ONNX model via Qualcomm AI Hub and copies it to
# hub/face_id/models/ ready for NPU inference.
#
# Usage:
#   .\setup_npu.ps1                     # prompts for token interactively
#   .\setup_npu.ps1 -Token YOUR_TOKEN   # pass token directly

param(
    [string]$Token   = "",
    [string]$Device  = "Snapdragon X Elite",
    [string]$Runtime = "onnx"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ModelsDir = Join-Path $ScriptDir "hub\face_id\models"
$ExportDir = Join-Path $env:TEMP "qonclave_export"

function Info  { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan   }
function Ok    { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green  }
function Fail  { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

# ── Step 1: Get AI Hub token ──────────────────────────────────────────────────

if (-not $Token) {
    Write-Host ""
    Write-Host "Qualcomm AI Hub token required." -ForegroundColor Cyan
    Write-Host "Sign up free at: https://workbench.aihub.qualcomm.com" -ForegroundColor Cyan
    Write-Host "Then go to: Account -> Settings -> API Token" -ForegroundColor Cyan
    Write-Host ""
    $Token = Read-Host "Enter your AI Hub API token"
    if (-not $Token) { Fail "No token provided." }
}

# ── Step 2: Install qai-hub-models if needed ──────────────────────────────────

Info "Installing qai-hub and qai-hub-models..."
pip install --quiet qai-hub "qai-hub-models[cavaface]" -c "$ScriptDir\hub\face_id\constraints.txt"
Ok "qai-hub-models ready"

# ── Step 3: Configure AI Hub with token ───────────────────────────────────────

Info "Configuring AI Hub..."
qai-hub configure --api_token $Token
if ($LASTEXITCODE -ne 0) { Fail "AI Hub configuration failed. Check your token." }
Ok "AI Hub configured"

# ── Step 4: Export CavaFace to ONNX via AI Hub ────────────────────────────────

Info "Exporting CavaFace for '$Device' (runtime: $Runtime)..."
Info "This submits a compile job to AI Hub cloud — takes 2-5 minutes..."

New-Item -ItemType Directory -Force -Path $ExportDir | Out-Null

Push-Location $ExportDir
qai-hub-models export cavaface `
    --target-runtime $Runtime `
    --device $Device `
    --output-dir "$ExportDir\export_assets"

if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "Export failed. Check your AI Hub account and device name." }
Pop-Location
Ok "Export complete"

# ── Step 5: Find exported ONNX files ─────────────────────────────────────────

Info "Locating exported model files..."

# Output layout: export_assets/CavaFace/CavaFace.onnx (+ optional .data, .encodings)
$onnxFile = Get-ChildItem "$ExportDir\export_assets" -Recurse -Filter "*.onnx" |
            Select-Object -First 1

if (-not $onnxFile) {
    # Some runtimes output a zip — unpack it
    $zipFile = Get-ChildItem "$ExportDir\export_assets" -Recurse -Filter "*.zip" |
               Select-Object -First 1
    if ($zipFile) {
        Info "Found zip archive: $($zipFile.Name) — extracting..."
        $unzipDir = "$ExportDir\unzipped"
        Expand-Archive -Path $zipFile.FullName -DestinationPath $unzipDir -Force
        $onnxFile = Get-ChildItem $unzipDir -Recurse -Filter "*.onnx" | Select-Object -First 1
    }
}

if (-not $onnxFile) {
    Fail "No .onnx file found in export output. Check $ExportDir\export_assets"
}
Ok "Found: $($onnxFile.FullName)"

# ── Step 6: Copy model files to hub/face_id/models/ ──────────────────────────

Info "Copying model files to $ModelsDir ..."
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null

# Copy .onnx
$dest = "$ModelsDir\CavaFace.onnx"
Copy-Item $onnxFile.FullName $dest -Force
Ok "Copied: CavaFace.onnx"

# Copy optional companion files (.data, .encodings) if present
foreach ($ext in @("*.data", "*.encodings")) {
    $companion = Get-ChildItem $onnxFile.DirectoryName -Filter $ext -ErrorAction SilentlyContinue |
                 Select-Object -First 1
    if ($companion) {
        $destName = "CavaFace$($companion.Extension)"
        Copy-Item $companion.FullName "$ModelsDir\$destName" -Force
        Ok "Copied: $destName"
    }
}

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " CavaFace NPU model ready!" -ForegroundColor Green
Write-Host ""
Write-Host " Model saved to: $ModelsDir" -ForegroundColor Cyan
Write-Host ""
Write-Host " Run with NPU:" -ForegroundColor Cyan
Write-Host "   cd hub\face_id" -ForegroundColor White
Write-Host "   .\run.ps1 identify -Image unknown.jpg -Npu" -ForegroundColor White
Write-Host "   .\run.ps1 benchmark -Image photo.jpg -Npu" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
