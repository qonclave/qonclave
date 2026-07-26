# Face identification pipeline — Windows (x86 and ARM64/WoS)
# Usage:
#   .\run.ps1 compare  image1.jpg image2.jpg
#   .\run.ps1 identify unknown.jpg
#   .\run.ps1 benchmark image.jpg

param(
    [string]$Mode = "help",
    [string]$Image1 = "",
    [string]$Image2 = "",
    [string]$Image  = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ── Install dependencies ──────────────────────────────────────────────────────
Write-Host "Checking dependencies..." -ForegroundColor Cyan
# mediapipe --no-deps: skips opencv-contrib which is only needed for the
# legacy mp.solutions API — we use mp.tasks which has no cv2 dependency
pip install --quiet numpy
pip install --quiet mediapipe --no-deps
pip install --quiet "qai-hub-models[cavaface]" pillow
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed. Make sure Python is in PATH." -ForegroundColor Red
    exit 1
}

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
    }
}
