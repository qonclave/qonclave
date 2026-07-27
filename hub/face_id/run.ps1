# run.ps1
# Run the face identification pipeline.
# Prerequisites: run .\setup.ps1 once first.
#
# Usage:
#   .\run.ps1 identify  -Image unknown.jpg
#   .\run.ps1 compare   -Image1 a.jpg -Image2 b.jpg
#   .\run.ps1 benchmark -Image photo.jpg
#   .\run.ps1 identify  -Image unknown.jpg -Npu   (Snapdragon X, after setup.ps1 -Npu)

param(
    [string]$Mode   = "help",
    [string]$Image1 = "",
    [string]$Image2 = "",
    [string]$Image  = "",
    [switch]$Npu
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$arch = $env:PROCESSOR_ARCHITECTURE

# ── Sanity check: setup.ps1 must have been run ────────────────────────────────

if (-not (Test-Path "$ScriptDir\face_detector.tflite")) {
    Write-Host "[ERROR] Setup not complete. Run .\setup.ps1 first." -ForegroundColor Red
    exit 1
}

if ($Npu -and $arch -ne "ARM64") {
    Write-Host "[WARN] -Npu is only supported on ARM64. Running CPU mode." -ForegroundColor Yellow
    $Npu = $false
}

if ($Npu -and -not (Test-Path "$ScriptDir\models\CavaFace.onnx")) {
    Write-Host "[ERROR] NPU models not found. Run .\setup.ps1 -Npu first." -ForegroundColor Red
    exit 1
}

$NpuFlag = if ($Npu) { "--npu" } else { "" }

# ── Run ───────────────────────────────────────────────────────────────────────

switch ($Mode.ToLower()) {
    "identify" {
        $img = if ($Image) { $Image } else { $Image1 }
        if (-not $img) {
            Write-Host "Usage: .\run.ps1 identify -Image <img> [-Npu]" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py $NpuFlag identify $img --db .\known_faces\
    }
    "compare" {
        if (-not $Image1 -or -not $Image2) {
            Write-Host "Usage: .\run.ps1 compare -Image1 <img1> -Image2 <img2> [-Npu]" -ForegroundColor Yellow
            exit 1
        }
        python face_pipeline.py $NpuFlag compare $Image1 $Image2
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
        Write-Host "  .\run.ps1 identify  -Image  <img>                  [-Npu]"
        Write-Host "  .\run.ps1 compare   -Image1 <img1> -Image2 <img2>  [-Npu]"
        Write-Host "  .\run.ps1 benchmark -Image  <img>                  [-Npu]"
        Write-Host ""
        Write-Host "  -Npu  Use Hexagon NPU (ARM64 only, requires .\setup.ps1 -Npu)"
        Write-Host ""
        Write-Host "First time? Run: .\setup.ps1"
        Write-Host ""
    }
}
