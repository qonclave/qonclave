# setup_npu.ps1
# Exports MediaPipe Face Detector + CavaFace ONNX models via Qualcomm AI Hub
# and copies them to hub/framework/face_id/models/ for full NPU inference.
#
# Both models run on Hexagon NPU (Snapdragon X Elite):
#   MediaPipeFace  face detection
#   CavaFace       ~4.3ms  (face embedding)
#   Total pipeline ~5-10ms (vs ~265ms CPU)
#
# MediaPipeFace.onnx is NOT a qai-hub-models catalog export. qai-hub-models'
# bundled mediapipe_face checkpoint ("blazefaceback.pth") measurably misses
# or under-scores turned/angled and distant faces versus Google's actual
# full_range weights (confidence 0.05-0.2 vs 0.6-0.9 on the same real-world
# test images) - so this script instead: downloads Google's official
# blaze_face_full_range.tflite, converts it to ONNX (tflite2onnx - NOT
# tf2onnx, which needs tensorflow, unavailable on ARM64 Windows), fixes a
# spec violation the converter introduces, and compiles THAT for NPU via
# qai_hub.submit_compile_job() directly (not the qai-hub-models CLI, since
# this model isn't in that package's catalog). See Export-FullRangeDetector
# below, and hub/framework/face_id/README.md's "Rebuilding the detector"
# section for the full manual walkthrough if you need to reproduce this
# outside this script.
#
#   AI Hub output structure (same for both models, catalog or custom):
#   <name>.onnx.zip
#     +-- job_<jobid>_optimized_onnx/
#           +-- model.onnx   (graph)
#           +-- model.data   (weights, referenced by model.onnx)
#
# Usage:
#   .\setup_npu.ps1                        # prompts for token
#   .\setup_npu.ps1 -Token YOUR_TOKEN      # pass token directly
#
# Reuse an already-completed AI Hub compile job instead of recompiling (skips
# the compile+profile+inference cloud queue, which is the slow part):
#   .\setup_npu.ps1 -Token YOUR_TOKEN -MediaPipeFaceJobId jXXXXXXXX -CavaFaceJobId jXXXXXXXX
# Job IDs only work with the AI Hub account/token that created them, and AI
# Hub may eventually garbage-collect old job artifacts. See README for the
# job IDs from this repo's own export (jg9dx40v5 = the full_range detector
# above; omitting -MediaPipeFaceJobId re-runs the full conversion from
# scratch rather than falling back to the old, less accurate catalog model).
#
# Normally called from setup.ps1, which passes -PythonPath so both scripts
# target the same environment (e.g. hub/geniex-env). Only pass it
# yourself when running setup_npu.ps1 directly against a non-default python:
#   .\setup_npu.ps1 -Token YOUR_TOKEN -PythonPath C:\path\to\python.exe
#
# Pass -Internal to route pip through Qualcomm's internal devpi mirror
# instead of pypi.org, on networks where files.pythonhosted.org is unreachable:
#   .\setup_npu.ps1 -Token YOUR_TOKEN -Internal

param(
    [string]$Token  = "",
    [string]$Device = "Snapdragon X Elite CRD",
    [string]$MediaPipeFaceJobId = "",   # reuse existing compile job for MediaPipeFace.onnx
    [string]$CavaFaceJobId      = "",   # reuse existing compile job for CavaFace.onnx
    [string]$PythonPath         = "",   # use this python.exe instead of resolving from PATH
                                         # (set by setup.ps1 to keep both scripts targeting
                                         # the same environment, e.g. hub/geniex-env)
    [switch]$Internal                   # route pip through Qualcomm's internal devpi mirror
)

$ErrorActionPreference = "Stop"
# This script lives in hub/framework/face_id/setup/, alongside constraints.txt; models/
# belongs to the face_id package one level up.
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir      = Split-Path -Parent $ScriptDir
$ModelsDir   = Join-Path $PkgDir "models"
$DownloadDir = Join-Path $env:TEMP "qonclave_npu_export"
$python      = if ($PythonPath) { $PythonPath } else { (Get-Command python).Source }
# -Internal routes every pip install below through Qualcomm's internal devpi
# mirror instead of pypi.org/files.pythonhosted.org.
$PipIndexArgs = if ($Internal) {
    @('--trusted-host', 'devpi.qualcomm.com', '-i', 'https://devpi.qualcomm.com/root/pypi/+simple/')
} else {
    @()
}
# A system install has python.exe at <root>\python.exe with entry-point
# scripts in a sibling <root>\Scripts\; a venv has python.exe ALREADY inside
# its own Scripts\ folder, with entry-point scripts as siblings of python.exe
# itself (no nested Scripts\Scripts\). sysconfig knows which layout this
# particular interpreter uses, so ask it instead of guessing from the path.
$ScriptsDir  = (& $python -c "import sysconfig; print(sysconfig.get_path('scripts'))").Trim()
$qaiHub      = Join-Path $ScriptsDir "qai-hub.exe"
$qaiHubModels = Join-Path $ScriptsDir "qai-hub-models.exe"

# Export clones small external repos (e.g. MediaPipePyTorch) and normally
# prompts for confirmation; QAIHM_CI skips that prompt for unattended runs.
$env:QAIHM_CI = "1"
# qai_hub's job-status spinner prints a unicode hourglass, which crashes on
# the default Windows console codepage (cp1252) unless stdout is UTF-8.
$env:PYTHONIOENCODING = "utf-8"

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

    if ($dataFile) {
        # The .onnx's external-data initializers reference the sidecar file by
        # its literal on-disk name (e.g. "model.data"). A plain rename/copy
        # leaves that internal reference stale, so onnxruntime fails to find
        # "<DestName>.data" at load time. Reload+resave via onnx rewrites the
        # reference to match the new external-data filename.
        # onnx's external-data writer APPENDS to the target file if it already
        # exists rather than truncating it - remove any stale copy first so
        # repeat runs don't silently bloat it.
        Remove-Item "$ModelsDir\$DestName.data" -Force -ErrorAction SilentlyContinue
        & $python -c @"
import onnx
m = onnx.load(r'$($onnxFile.FullName)', load_external_data=True)
onnx.save_model(m, r'$ModelsDir\$DestName.onnx', save_as_external_data=True, all_tensors_to_one_file=True, location='$DestName.data')
"@
        if ($LASTEXITCODE -ne 0) { Fail "Failed to rewrite external-data reference for $DestName" }
        Ok "Copied: $DestName.onnx"
        Ok "Copied: $DestName.data  ($([math]::Round((Get-Item "$ModelsDir\$DestName.data").Length/1MB)) MB)"
    } else {
        Copy-Item $onnxFile.FullName "$ModelsDir\$DestName.onnx" -Force
        Ok "Copied: $DestName.onnx  ($([math]::Round($onnxFile.Length/1KB)) KB)"
    }
}

function Download-From-Job {
    param([string]$JobId, [string]$DestName)

    Info "Reusing existing compile job $JobId for $DestName (skipping recompile)..."
    New-Item -ItemType Directory -Force -Path "$DownloadDir\$DestName" | Out-Null

    $out = & $python -c @"
import sys
import qai_hub as hub
try:
    job = hub.get_job('$JobId')
except Exception as e:
    print(f'ERROR: Could not find job $JobId - {e}', file=sys.stderr)
    sys.exit(1)
status = job.get_status().code
if status != 'SUCCESS':
    print(f'ERROR: Job $JobId is not complete (status: {status}).', file=sys.stderr)
    sys.exit(2)
path = job.download_target_model(r'$DownloadDir\$DestName')
print(path)
"@
    if ($LASTEXITCODE -eq 1) { Fail "Job '$JobId' not found on AI Hub. Check the job ID and that it belongs to this account." }
    if ($LASTEXITCODE -eq 2) { Fail "Job '$JobId' is not complete yet. Check https://workbench.aihub.qualcomm.com/jobs/$JobId/" }
    if ($LASTEXITCODE -ne 0) { Fail "Download failed for job '$JobId' (exit $LASTEXITCODE)." }

    $zipPath = ($out | Select-Object -Last 1).Trim()
    Extract-And-Copy -ZipPath $zipPath -DestName $DestName
}

function Export-Model {
    param([string]$ModelName, [string]$DestName, [string[]]$ExtraArgs = @())

    Info "Exporting $ModelName for '$Device' (onnx)..."
    New-Item -ItemType Directory -Force -Path "$DownloadDir\$DestName" | Out-Null

    Push-Location "$DownloadDir\$DestName"
    # Profiling/inference/summary run real accuracy comparisons on a device and
    # aren't needed to get the compiled .onnx artifact; skipping them avoids a
    # long device-queue wait and a crash in the CLI's summary step (a small
    # .h5 reference-output download during accuracy comparison throws before
    # printing a real error).
    # Note: deliberately NOT merging stderr (no 2>&1) - PowerShell 5.1 wraps a
    # native command's stderr lines as terminating NativeCommandErrors under
    # $ErrorActionPreference = "Stop", aborting on the first warning even on
    # exit code 0. Stdout alone is enough: "Scheduled compile job (...)" prints
    # there.
    $outLines = & $qaiHubModels export $ModelName `
        --target-runtime onnx `
        --precision float `
        --device $Device `
        --skip-profiling --skip-inferencing --skip-summary `
        @ExtraArgs `
        --output-dir "$DownloadDir\$DestName\export_assets"
    $exportExit = $LASTEXITCODE
    Pop-Location

    $zipFile = Get-ChildItem "$DownloadDir\$DestName\export_assets" -Recurse -Filter "*.onnx.zip" -ErrorAction SilentlyContinue |
               Select-Object -First 1

    if (-not $zipFile) {
        # The CLI can crash in its own post-export bookkeeping (e.g. a
        # get_output_spec()/metadata mismatch bug in qai-hub-models 0.58.0
        # when --include-detector-postprocessing is used) even though the
        # compile job it scheduled actually succeeded. Recover by pulling
        # that job directly instead of failing the whole export.
        $jobIdLine = $outLines | Select-String -Pattern "Scheduled compile job \((j[a-z0-9]+)\)" | Select-Object -Last 1
        if ($jobIdLine) {
            $jobId = $jobIdLine.Matches[0].Groups[1].Value
            Info "Export CLI didn't finish cleanly, but compile job $jobId was scheduled - downloading it directly..."
            Download-From-Job -JobId $jobId -DestName $DestName
            return
        }
        if ($exportExit -ne 0) { Fail "Export of $ModelName failed." }
        Fail "No .onnx.zip found for $ModelName"
    }

    Ok "$ModelName export complete"
    Extract-And-Copy -ZipPath $zipFile.FullName -DestName $DestName
}

function Export-FullRangeDetector {
    param([string]$DestName = "MediaPipeFace")

    # See header comment for why this doesn't use qai-hub-models' mediapipe_face
    # catalog model at all: Google's real full_range weights, converted and
    # compiled from scratch, measurably detect more real-world faces.
    Info "Building full_range face detector (custom TFLite conversion, not qai-hub-models catalog)..."
    $workDir = "$DownloadDir\$DestName"
    New-Item -ItemType Directory -Force -Path $workDir | Out-Null

    $tflitePath    = "$workDir\blaze_face_full_range.tflite"
    $onnxRawPath   = "$workDir\blaze_face_full_range.onnx"
    $onnxFixedPath = "$workDir\blaze_face_full_range_fixed.onnx"

    if (-not (Test-Path $tflitePath)) {
        Info "Downloading Google's full_range BlazeFace TFLite model..."
        Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/1/blaze_face_full_range.tflite" -OutFile $tflitePath
    }

    Info "Converting TFLite -> ONNX (tflite2onnx)..."
    & $python -c @"
import tflite2onnx
tflite2onnx.convert(r'$tflitePath', r'$onnxRawPath')
"@
    if ($LASTEXITCODE -ne 0) { Fail "TFLite->ONNX conversion failed for full_range detector." }

    Info "Fixing ONNX value_info spec violation (tflite2onnx duplicates input/output names there)..."
    & $python -c @"
import onnx
m = onnx.load(r'$onnxRawPath')
io_names = {t.name for t in m.graph.input} | {t.name for t in m.graph.output}
kept = [vi for vi in m.graph.value_info if vi.name not in io_names]
del m.graph.value_info[:]
m.graph.value_info.extend(kept)
onnx.checker.check_model(m)
onnx.save(m, r'$onnxFixedPath')
"@
    if ($LASTEXITCODE -ne 0) { Fail "ONNX value_info fix failed for full_range detector." }

    Info "Submitting AI Hub compile job for '$Device'..."
    $jobIdOut = & $python -c @"
import qai_hub as hub
device = hub.Device('$Device')
job = hub.submit_compile_job(
    model=r'$onnxFixedPath',
    device=device,
    name='blaze_face_full_range_qonclave',
    options='--target_runtime onnx',
)
print(job.job_id)
"@
    if ($LASTEXITCODE -ne 0 -or -not $jobIdOut) { Fail "Failed to submit AI Hub compile job for full_range detector." }
    $jobId = ($jobIdOut | Select-Object -Last 1).Trim()
    Info "Compile job submitted: $jobId (https://workbench.aihub.qualcomm.com/jobs/$jobId/)"

    Info "Waiting for compile job to finish (usually 1-3 minutes)..."
    & $python -c @"
import sys, time
import qai_hub as hub
job = hub.get_job('$jobId')
status = job.get_status()
for _ in range(90):
    status = job.get_status()
    if status.code not in ('CREATED', 'OPTIMIZING_MODEL', 'QUEUED', 'RUNNING'):
        break
    time.sleep(10)
else:
    print('ERROR: timed out waiting for compile job', file=sys.stderr)
    sys.exit(1)
if status.code != 'SUCCESS':
    print(f'ERROR: compile job failed: {status.code} {status.message}', file=sys.stderr)
    sys.exit(1)
"@
    if ($LASTEXITCODE -ne 0) { Fail "Compile job $jobId for full_range detector did not succeed. Check https://workbench.aihub.qualcomm.com/jobs/$jobId/" }
    Ok "Compile job $jobId succeeded"

    Download-From-Job -JobId $jobId -DestName $DestName
}

# Step 1: Get token

if (-not $Token) {
    Write-Host ""
    Write-Host "Qualcomm AI Hub token required." -ForegroundColor Cyan
    Write-Host "Sign up free at  : https://workbench.aihub.qualcomm.com" -ForegroundColor Cyan
    Write-Host "Get your token   : Account -> Settings -> API Token" -ForegroundColor Cyan
    Write-Host ""
    $Token = Read-Host "Enter your AI Hub API token"
    if (-not $Token) { Fail "No token provided." }
}

# Step 2: Install and configure

$reuseBoth = $MediaPipeFaceJobId -and $CavaFaceJobId
if ($reuseBoth) {
    # Both models are being pulled from existing jobs - only the thin qai_hub
    # client (plus onnx, to fix up external-data references after download) is
    # needed, not the full qai-hub-models dependency stack (torch, etc).
    Info "Both job IDs provided - installing only qai_hub + onnx (skipping qai-hub-models)..."
    & $python -m pip install "qai_hub>=0.51.0" "onnx<=1.18.0,>=1.17" -c "$ScriptDir\constraints.txt" @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
} elseif ($MediaPipeFaceJobId) {
    # MediaPipeFace reused from a job; CavaFace still needs a fresh export.
    Info "Installing qai-hub-models (CavaFace fresh export)..."
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
        --extra-index-url https://download.pytorch.org/whl/cpu -c "$ScriptDir\constraints.txt" @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
    & $python -m pip install "qai-hub-models[cavaface]==0.58.0" --no-deps @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
} else {
    Info "Installing qai-hub-models..."
    # See setup.ps1 for why this is split: win_arm64+cp313 has no onnxruntime
    # wheel below 1.24.2 (conflicts with qai-hub-models' onnxruntime<1.23 pin) and
    # no torch/torchvision wheels on PyPI at all (only download.pytorch.org).
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
        --extra-index-url https://download.pytorch.org/whl/cpu -c "$ScriptDir\constraints.txt" @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
    # Only [cavaface] -- mediapipe_face's catalog checkpoint is deliberately
    # not used (see header comment); the full_range detector conversion below
    # needs tflite2onnx/tflite instead, installed next regardless of branch.
    & $python -m pip install "qai-hub-models[cavaface]==0.58.0" --no-deps @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}

if (-not $MediaPipeFaceJobId) {
    # Needed only when actually (re)converting the full_range detector from
    # TFLite; skipped when -MediaPipeFaceJobId reuses an already-compiled job.
    Info "Installing tflite2onnx (full_range detector conversion)..."
    & $python -m pip install tflite2onnx tflite @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}
Ok "Packages ready"

Info "Configuring AI Hub..."
& $qaiHub configure --api_token $Token
if ($LASTEXITCODE -ne 0) { Fail "AI Hub configuration failed. Check your token." }
Ok "AI Hub configured"

# Step 3: Create output dirs

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $ModelsDir   | Out-Null

# qai-hub-models 0.58.0's own info.yaml "is this model published" validator
# requires a release-assets.yaml sidecar file next to each model that the
# PyPI wheel simply doesn't ship - every model's export crashes with
# "no release assets available" until one exists (content is never read,
# only its presence is checked). This is a fresh-environment gotcha, not a
# one-time fix: it recurs in ANY new venv qai-hub-models gets installed
# into (e.g. hub/geniex-env, a teammate's machine, CI), so create it
# here rather than requiring a manual patch each time. No-ops harmlessly
# if qai_hub_models isn't installed (pure job-ID-reuse mode needs only
# bare qai_hub).
& $python -c @"
try:
    from qai_hub_models.utils.path_helpers import QAIHM_MODELS_ROOT
    for model_id in ('mediapipe_face', 'cavaface'):
        p = QAIHM_MODELS_ROOT / model_id / 'release-assets.yaml'
        if not p.exists():
            p.touch()
except ImportError:
    pass
"@ 2>$null

# Step 4: Export both models (or download from reused jobs)

Write-Host ""
Write-Host "Exporting 2 models - this takes ~5-10 minutes total..." -ForegroundColor Cyan
Write-Host ""

if ($MediaPipeFaceJobId) {
    Download-From-Job -JobId $MediaPipeFaceJobId -DestName "MediaPipeFace"
} else {
    # NOT qai-hub-models' mediapipe_face catalog export -- see header comment
    # and Export-FullRangeDetector for why. Outputs are raw per-anchor
    # regressor/classifier tensors; face_pipeline.py's _detect_faces_npu()
    # does the anchor-decode + sigmoid + NMS by hand to match.
    Export-FullRangeDetector -DestName "MediaPipeFace"
}

if ($CavaFaceJobId) {
    Download-From-Job -JobId $CavaFaceJobId -DestName "CavaFace"
} else {
    Export-Model -ModelName "cavaface" -DestName "CavaFace"
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Full NPU pipeline ready!" -ForegroundColor Green
Write-Host ""
Write-Host " Models in: $ModelsDir" -ForegroundColor Cyan
Write-Host "   MediaPipeFace.onnx  (full_range face detector, NPU)" -ForegroundColor White
Write-Host "   MediaPipeFace.data" -ForegroundColor White
Write-Host "   CavaFace.onnx       (face embedding ~4.3ms NPU)" -ForegroundColor White
Write-Host "   CavaFace.data       (weights ~250MB)" -ForegroundColor White
Write-Host ""
Write-Host " Run with NPU:" -ForegroundColor Cyan
Write-Host "   python face_pipeline.py --npu identify  unknown.jpg" -ForegroundColor White
Write-Host "   python face_pipeline.py --npu benchmark photo.jpg" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
