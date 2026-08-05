<#
    setup_pose.ps1 - export HRNetPose from Qualcomm AI Hub and compile it for the
    Hexagon NPU.

    Mirrors face_id/setup/setup_npu.ps1 deliberately: same token handling, same
    job-reuse flag, same "the .onnx references its .data sidecar by literal
    filename" rename fix. Pose follows the face_id precedent, not the vlm one -
    HRNetPose is a CNN returning a heatmap, so it takes the ONNX Runtime + QNN
    path this repo already uses, not GenieX's generative runtime.

    NOTHING IS VENDORED. hrnet_pose.data is 109 MB and hrnet_pose_ctx.onnx is
    tied to the exact QAIRT build and HTP architecture that produced it, so both
    are generated here and gitignored. See ..\models\README.txt.

    Usage:
      .\setup_pose.ps1                                   # prompts for a token
      .\setup_pose.ps1 -Token YOUR_TOKEN
      .\setup_pose.ps1 -Token YOUR_TOKEN -HrnetPoseJobId jXXXXXXXX
      .\setup_pose.ps1 -PythonPath C:\path\to\python.exe  # target a venv
      .\setup_pose.ps1 -Internal                         # Qualcomm devpi mirror
      .\setup_pose.ps1 -SkipCompile                      # export only

    A free token: https://workbench.aihub.qualcomm.com -> Account -> Settings
    -> API Token.
#>

param(
    [string]$Token = '',
    [string]$HrnetPoseJobId = '',
    [string]$PythonPath = '',
    [string]$Device = 'Snapdragon X Elite',
    [switch]$Internal,
    [switch]$SkipCompile
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

function Info($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "    [ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }

$ModelsDir   = Join-Path (Split-Path $PSScriptRoot -Parent) 'models'
$DownloadDir = Join-Path $env:TEMP 'qonclave-pose-export'
$PipIndexArgs = if ($Internal) {
    @('--trusted-host', 'devpi.qualcomm.com', '-i', 'https://devpi.qualcomm.com/root/pypi/+simple/')
} else { @() }

$python = if ($PythonPath) { $PythonPath } else { (Get-Command python).Source }
Info "Using Python: $python"
New-Item -ItemType Directory -Force -Path $ModelsDir, $DownloadDir | Out-Null

# --- 1. Dependencies ---------------------------------------------------------
Info 'Ensuring qai-hub-models and onnx are installed'
& $python -m pip install -q qai-hub-models onnx @PipIndexArgs
if ($LASTEXITCODE -ne 0) { throw 'Could not install qai-hub-models / onnx.' }
Ok 'dependencies present'

# --- 2. Token ----------------------------------------------------------------
if (-not $Token) {
    $Token = Read-Host 'Qualcomm AI Hub API token (https://workbench.aihub.qualcomm.com)'
}
if (-not $Token) { throw 'An AI Hub token is required; there are no vendored model files.' }
& $python -m qai_hub_models.configure --api_token $Token 2>$null
& $python -c "import qai_hub" 2>$null
if ($LASTEXITCODE -ne 0) { throw 'qai_hub is not importable after configure.' }
Ok 'AI Hub configured'

# --- 3. Export or reuse a compile job ---------------------------------------
$exported = $null

if ($HrnetPoseJobId) {
    Info "Reusing compile job $HrnetPoseJobId (skipping recompile)"
    $exported = & $python -c @"
import sys
import qai_hub as hub
try:
    job = hub.get_job('$HrnetPoseJobId')
except Exception as e:
    print(f'ERROR: could not find job $HrnetPoseJobId - {e}', file=sys.stderr); sys.exit(1)
status = job.get_status().code
if status != 'SUCCESS':
    print(f'ERROR: job $HrnetPoseJobId is not complete (status: {status})', file=sys.stderr); sys.exit(2)
print(job.download_target_model(r'$DownloadDir'))
"@
    if ($LASTEXITCODE -ne 0) { throw "Could not download job $HrnetPoseJobId." }
} else {
    Info "Exporting hrnet_pose for '$Device' (this schedules a cloud compile; several minutes)"
    Push-Location $DownloadDir
    try {
        & $python -m qai_hub_models.models.hrnet_pose.export --target-runtime onnx --device "$Device"
    } finally {
        Pop-Location
    }
    # The export CLI sometimes exits untidily after the job is already scheduled
    # and downloaded. Recover by looking for the artifact rather than trusting
    # the exit code - the same recovery path setup_npu.ps1 uses.
    $found = Get-ChildItem -Path $DownloadDir -Recurse -Filter 'model.onnx' -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if (-not $found) {
        throw ("Export produced no model.onnx under $DownloadDir. If a job WAS scheduled, " +
               "re-run with -HrnetPoseJobId <id> to download it without recompiling.")
    }
    $exported = $found.FullName
}
Ok "exported: $exported"

# --- 4. Rename via onnx.save_model ------------------------------------------
# A plain file rename breaks the model: the .onnx references its .data sidecar
# BY LITERAL FILENAME, so renaming one without rewriting the other produces an
# opaque load error. Rewrite the reference instead.
Info 'Installing as hrnet_pose.onnx + hrnet_pose.data'
& $python -c @"
import onnx, pathlib
src = pathlib.Path(r'$exported')
dst = pathlib.Path(r'$ModelsDir') / 'hrnet_pose.onnx'
model = onnx.load(str(src))
onnx.save_model(model, str(dst), save_as_external_data=True,
                location='hrnet_pose.data', all_tensors_to_one_file=True,
                size_threshold=0)
print(f'wrote {dst}')
"@
if ($LASTEXITCODE -ne 0) { throw 'onnx.save_model failed; the .data reference was not rewritten.' }

# Carry the quantization metadata across if the export produced any.
$meta = Get-ChildItem -Path $DownloadDir -Recurse -Filter 'metadata.json' -ErrorAction SilentlyContinue |
        Select-Object -First 1
if ($meta) {
    Copy-Item $meta.FullName (Join-Path $ModelsDir 'metadata.json') -Force
    Ok 'metadata.json copied'
} else {
    Warn 'no metadata.json in the export; pose_pipeline falls back to its recorded scales'
}
Ok 'model installed'

# --- 5. Compile the HTP context binary --------------------------------------
if ($SkipCompile) {
    Warn '-SkipCompile set; session init will be ~6.0s instead of ~0.30s'
} else {
    Info 'Compiling the HTP context binary (~4.6s)'
    & $python -c @"
import onnxruntime as ort, onnxruntime_qnn as qnn, pathlib, sys

models = pathlib.Path(r'$ModelsDir')
src, dst = models / 'hrnet_pose.onnx', models / 'hrnet_pose_ctx.onnx'
try:
    ort.register_execution_provider_library(qnn.get_ep_name(), qnn.get_library_path())
except Exception:
    pass

so = ort.SessionOptions()
npu = [d for d in ort.get_ep_devices()
       if d.ep_name == qnn.get_ep_name() and d.device.type == ort.OrtHardwareDeviceType.NPU]
if not npu:
    print('ERROR: no QNN NPU device found; cannot compile a context binary', file=sys.stderr)
    sys.exit(1)
so.add_provider_for_devices(npu, {'backend_path': qnn.get_qnn_htp_path()})

# ModelCompiler defaults to ORT_DISABLE_ALL, which fails with an NHWC
# layout-transform error along the lines of
#   'Conv_token_61 ... com.ms.internal.nhwc ... not selected by that EP'.
# That is the default optimization level, not a broken export.
so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

compiler = ort.ModelCompiler(so, str(src), embed_compiled_data_into_model=True)
compiler.compile_to_file(str(dst))
print(f'wrote {dst}')
"@
    if ($LASTEXITCODE -ne 0) {
        Warn 'Context-binary compile failed. Pose still works from the raw model, just with a ~6s session init.'
    } else {
        Ok 'context binary built'
    }
}

# --- 6. Verify ---------------------------------------------------------------
Info 'Verifying'
& $python -c @"
import sys, pathlib
sys.path.insert(0, r'$(Split-Path (Split-Path $PSScriptRoot -Parent) -Parent)')
from pose.pose import PoseBackend
backend = PoseBackend()
backend.warmup()
status = backend.status()
print('  pose status:', status)
if not status['available']:
    print('ERROR: pose did not load', file=sys.stderr); sys.exit(1)
if status['mode'] != 'npu':
    print('WARNING: pose resolved to', status['mode'], '- expected npu. The QNN EP did not bind;',
          'the session builder falls back silently by design.', file=sys.stderr)
"@
if ($LASTEXITCODE -ne 0) { throw 'Pose model did not load after setup.' }

Write-Host "`n===================================================================" -ForegroundColor Green
Write-Host ' Pose ready.' -ForegroundColor Green
Write-Host '   Benchmark: python hub\framework\pose\pose_pipeline.py benchmark <crop.jpg>' -ForegroundColor Green
Write-Host '   Expect ~0.30s session init, ~1.45 ms/inference, mode: npu' -ForegroundColor Green
Write-Host "===================================================================" -ForegroundColor Green
