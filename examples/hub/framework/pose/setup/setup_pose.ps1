# setup_pose.ps1
# Exports the HRNetPose w8a8 ONNX model via Qualcomm AI Hub, copies it to
# hub/framework/pose/models/, and bakes the per-host HTP context binary.
#
# HRNetPose runs on the Hexagon NPU (Snapdragon X Elite) at ~1.4ms/inference
# (vs ~45ms CPU). The context binary (hrnet_pose_ctx.onnx) cuts session init
# from ~6.0s to ~0.3s and is SDK/HTP-specific: it is compiled HERE, on the
# target host, never downloaded or committed.
#
#   AI Hub output structure (same as the face-ID exports):
#   <name>.onnx.zip
#     +-- job_<jobid>_optimized_onnx/
#           +-- model.onnx   (graph)
#           +-- model.data   (weights, referenced by model.onnx)
#
# Usage:
#   .\setup_pose.ps1                        # prompts for token
#   .\setup_pose.ps1 -Token YOUR_TOKEN      # pass token directly
#
# Reuse an already-completed AI Hub compile job instead of recompiling (skips
# the compile cloud queue, which is the slow part):
#   .\setup_pose.ps1 -Token YOUR_TOKEN -HrnetPoseJobId jXXXXXXXX
# Job IDs only work with the AI Hub account/token that created them.
#
# Normally called from hub/setup_hub.ps1 (non-fatal on failure: the hub then
# reports pose as unavailable while everything else keeps working). Pass
# -PythonPath when running directly against a non-default python:
#   .\setup_pose.ps1 -Token YOUR_TOKEN -PythonPath C:\path\to\python.exe
#
# Pass -Internal to route pip through Qualcomm's internal devpi mirror
# instead of pypi.org, on networks where files.pythonhosted.org is unreachable.

param(
    [string]$Token  = "",
    [string]$Device = "Snapdragon X Elite CRD",
    [string]$HrnetPoseJobId = "",   # reuse existing compile job for hrnet_pose.onnx
    [string]$PythonPath     = "",   # use this python.exe instead of resolving from PATH
    [switch]$SkipCompile,           # skip the HTP context-binary bake (raw model still works)
    [switch]$Internal               # route pip through Qualcomm's internal devpi mirror
)

$ErrorActionPreference = "Stop"
# This script lives in hub/framework/pose/setup/; models/ belongs to the pose
# package one level up.
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PkgDir      = Split-Path -Parent $ScriptDir
$FrameworkDir = Split-Path -Parent $PkgDir
$ModelsDir   = Join-Path $PkgDir "models"
$OpenCvWheelsDir = Join-Path $FrameworkDir "face_id\wheels"
$DownloadDir = Join-Path $env:TEMP "qonclave_npu_export"
$python      = if ($PythonPath) { $PythonPath } else { (Get-Command python).Source }
$PipIndexArgs = if ($Internal) {
    @('--trusted-host', 'devpi.qualcomm.com', '-i', 'https://devpi.qualcomm.com/root/pypi/+simple/')
} else {
    @()
}
# See face_id/setup/setup_npu.ps1 for why sysconfig (venv vs system layout).
$ScriptsDir  = (& $python -c "import sysconfig; print(sysconfig.get_path('scripts'))").Trim()
$qaiHub      = Join-Path $ScriptsDir "qai-hub.exe"
$qaiHubModels = Join-Path $ScriptsDir "qai-hub-models.exe"

$env:QAIHM_CI = "1"
$env:PYTHONIOENCODING = "utf-8"

function Info { param($m) Write-Host "[INFO]  $m" -ForegroundColor Cyan   }
function Ok   { param($m) Write-Host "[ OK ]  $m" -ForegroundColor Green  }
function Fail { param($m) Write-Host "[FAIL]  $m" -ForegroundColor Red; exit 1 }

function Extract-And-Copy {
    param([string]$ZipPath, [string]$DestName)

    $unzipDir = "$DownloadDir\extracted_$DestName"
    # Clear stale extractions: $DownloadDir persists across runs and each job
    # extracts into its own job_<jobid>_optimized_onnx/ subfolder — without
    # this, Get-ChildItem picks whichever old job sorts first, silently
    # reinstalling a stale model (see face_id/setup/setup_npu.ps1's history).
    Remove-Item $unzipDir -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $ZipPath -DestinationPath $unzipDir -Force

    $onnxFiles = @(Get-ChildItem $unzipDir -Recurse -Filter "model.onnx")
    if ($onnxFiles.Count -gt 1) {
        Fail "Expected exactly one model.onnx in $ZipPath, found $($onnxFiles.Count) under $unzipDir"
    }
    $onnxFile = $onnxFiles | Select-Object -First 1
    $dataFile = Get-ChildItem $unzipDir -Recurse -Filter "model.data" | Select-Object -First 1

    if (-not $onnxFile) { Fail "model.onnx not found in $ZipPath" }

    if ($dataFile) {
        # The .onnx's external-data initializers reference the sidecar by its
        # literal on-disk name; a plain rename leaves that stale. Reload+resave
        # via onnx rewrites the reference. onnx's external-data writer APPENDS
        # if the target exists — remove any stale copy first.
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

    Info "Exporting $ModelName for '$Device' (onnx, w8a8)..."
    New-Item -ItemType Directory -Force -Path "$DownloadDir\$DestName" | Out-Null

    Push-Location "$DownloadDir\$DestName"
    # Skip profiling/inference/summary: not needed for the compiled artifact
    # (see face_id/setup/setup_npu.ps1 for the full rationale, including why
    # stderr is deliberately NOT merged here).
    $outLines = & $qaiHubModels export $ModelName `
        --target-runtime onnx `
        --device $Device `
        --skip-profiling --skip-inferencing --skip-summary `
        @ExtraArgs `
        --output-dir "$DownloadDir\$DestName\export_assets"
    $exportExit = $LASTEXITCODE
    Pop-Location

    $zipFile = Get-ChildItem "$DownloadDir\$DestName\export_assets" -Recurse -Filter "*.onnx.zip" -ErrorAction SilentlyContinue |
               Select-Object -First 1

    if (-not $zipFile) {
        # The CLI can crash in its own post-export bookkeeping even though the
        # compile job it scheduled succeeded — recover by pulling that job.
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

# Pose and its OpenMMLab export dependencies import cv2. PyPI does not provide
# an opencv-python wheel for Windows ARM64, so install the repository's
# matching opencv-python-headless wheel explicitly. Both distributions expose
# the same cv2 module; mmcv-lite is installed with --no-deps below so pip does
# not try to replace this wheel with an opencv-python source build.
Info "Installing repository OpenCV wheel for pose..."
$pyTag = (& $python -c "import sys; print(f'cp{sys.version_info[0]}{sys.version_info[1]}')").Trim()
$openCvWheel = Get-ChildItem $OpenCvWheelsDir -Filter "opencv_python_headless-*-$pyTag-$pyTag-win_arm64.whl" |
               Sort-Object Name -Descending | Select-Object -First 1
if (-not $openCvWheel) {
    Fail "Missing ARM64 OpenCV wheel for $pyTag under $OpenCvWheelsDir."
}
& $python -m pip install $openCvWheel.FullName @PipIndexArgs
if ($LASTEXITCODE -ne 0) { Fail "pip install failed for local OpenCV wheel: $($openCvWheel.Name)" }
Ok "OpenCV ready from local wheel: $($openCvWheel.Name)"

if ($HrnetPoseJobId) {
    # Model pulled from an existing job — only the thin qai_hub client (plus
    # onnx, to fix up the external-data reference) is needed.
    Info "Job ID provided - installing only qai_hub + onnx (skipping qai-hub-models)..."
    & $python -m pip install "qai_hub>=0.51.0" "onnx<=1.18.0,>=1.17" @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
} else {
    Info "Installing qai-hub-models (hrnet_pose fresh export)..."
    # Same split-install as face_id/setup/setup_npu.ps1 (win_arm64 wheel
    # availability). Keep hrnet_pose's model-specific extras explicit because
    # qai-hub-models itself is installed with --no-deps below. Do not let
    # mmcv-lite resolve its `opencv-python` dependency: PyPI has no compatible
    # Windows ARM64 wheel and would try a blocked native build. The local
    # opencv-python-headless wheel installed above provides the same cv2 module.
    & $python -m pip install `
        "huggingface_hub<2.0,>=0.34.0" `
        "numpy<=2.4.4" "onnx<=1.18.0,>=1.17" `
        "torch<=2.11.0,>=2.4" "torchvision<=0.26.0,>=0.19" `
        "typing-extensions<=4.15.0,>=4.12.2" "tqdm<=4.67.3,>=4.66" `
        "qai_hub>=0.51.0" "filelock<=3.29.0,>=3.16.1" `
        inputimeout "pydantic<=2.13.3,>=2" pydantic_yaml `
        "packaging<=26.2.0,>24.2" platformdirs "qai_hub_models_cli==0.58.0" `
        yacs addict yapf termcolor rich regex json-tricks `
        pycocotools terminaltables `
        gitpython pillow schema requests_toolbelt "httpx<=0.28.1,>=0.27" `
        gdown boto3 "boto3-stubs[s3]" numpydoc pandas `
        tabulate ipython scipy coverage `
        --extra-index-url https://download.pytorch.org/whl/cpu @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
    # chumpy is pure Python but its legacy setup.py imports pip while resolving
    # build requirements. Modern pip's isolated build environment omits pip,
    # so install it separately without build isolation.
    & $python -m pip install chumpy --no-build-isolation @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed for chumpy." }
    # These OpenMMLab packages declare `opencv-python` by distribution name.
    # Install them together without dependency resolution so they use the
    # repository's already-installed opencv-python-headless/cv2 instead.
    # mmpose is a pre-build requirement documented in qai-hub-models' bundled
    # HRNet Pose README but is not represented by its hrnet-pose extra metadata.
    & $python -m pip install `
        "mmengine==0.10.7" "mmdet==3.3.0" "mmcv-lite==2.1.0" "mmpose==1.2.0" `
        --no-deps @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed for HRNet Pose OpenMMLab dependencies." }
    & $python -m pip install "qai-hub-models==0.58.0" --no-deps @PipIndexArgs
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed." }
}
Ok "Packages ready"

# Import exactly what the CLI imports before configuring AI Hub or submitting
# a cloud job. This turns missing exporter dependencies into an immediate,
# local setup error instead of discovering them after authentication.
if (-not $HrnetPoseJobId) {
    Info "Verifying HRNet Pose exporter imports..."
    & $python -c "import qai_hub_models.models.hrnet_pose.export; print('  HRNet Pose exporter import OK')"
    if ($LASTEXITCODE -ne 0) { Fail "HRNet Pose exporter dependency check failed." }
}

Info "Configuring AI Hub..."
& $qaiHub configure --api_token $Token
if ($LASTEXITCODE -ne 0) { Fail "AI Hub configuration failed. Check your token." }
Ok "AI Hub configured"

# Step 3: Create output dirs

New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
New-Item -ItemType Directory -Force -Path $ModelsDir   | Out-Null

# qai-hub-models 0.58.0's release-assets.yaml gotcha (presence-checked, never
# read) — same workaround as face_id/setup/setup_npu.ps1, fresh-env recurring.
& $python -c @"
try:
    from qai_hub_models.utils.path_helpers import QAIHM_MODELS_ROOT
    p = QAIHM_MODELS_ROOT / 'hrnet_pose' / 'release-assets.yaml'
    if not p.exists():
        p.touch()
except ImportError:
    pass
"@ 2>$null

# Step 4: Export the model (or download from a reused job)

if ($HrnetPoseJobId) {
    Download-From-Job -JobId $HrnetPoseJobId -DestName "hrnet_pose"
} else {
    # w8a8: the quantized variant is what hits ~1.4ms on the HTP; the float
    # export is a different (slower) artifact with different quant metadata.
    Export-Model -ModelName "hrnet_pose" -DestName "hrnet_pose" -ExtraArgs @("--precision", "w8a8")
}

# Step 5: Bake the per-host HTP context binary

if ($SkipCompile) {
    Info "Skipping context-binary compile (-SkipCompile); the raw model will be used (slower session init)."
} else {
    Info "Compiling HTP context binary (per-host, ~5s)..."
    & $python "$PkgDir\pose_pipeline.py" compile
    if ($LASTEXITCODE -ne 0) {
        # Non-fatal: the raw-model fallback works, just with a ~6s session init.
        Write-Host "[WARN]  Context-binary compile failed - pose will fall back to the raw model (slower startup)." -ForegroundColor Yellow
    } else {
        Ok "Context binary ready: hrnet_pose_ctx.onnx"
    }
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " Pose backend ready!" -ForegroundColor Green
Write-Host ""
Write-Host " Models in: $ModelsDir" -ForegroundColor Cyan
Write-Host "   hrnet_pose.onnx      (HRNetPose w8a8 graph)" -ForegroundColor White
Write-Host "   hrnet_pose.data      (weights ~110MB)" -ForegroundColor White
Write-Host "   hrnet_pose_ctx.onnx  (HTP context binary, per-host)" -ForegroundColor White
Write-Host ""
Write-Host " Verify:" -ForegroundColor Cyan
Write-Host "   python $PkgDir\pose_pipeline.py benchmark photo.jpg" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
