"""
qnn_session.py — the repo's single entry point for creating an onnxruntime
InferenceSession on the Hexagon NPU via onnxruntime-qnn.

onnxruntime's QNN support is a dynamically-registered "plugin" execution
provider (added in the 1.20+ device-based EP API): the provider library must
be registered by path, then bound to the actual NPU OrtEpDevice via
SessionOptions.add_provider_for_devices — passing "QNNExecutionProvider" as a
plain string to InferenceSession(providers=...) silently no-ops and falls
back to CPU on this onnxruntime version.

Used by both face_id/face_pipeline.py (which keeps a thin _qnn_session alias
for backward compatibility) and pose/pose_pipeline.py. Falls back to
CPUExecutionProvider on any failure — callers that need to report the real
execution mode must use session_mode(), which inspects the session's resolved
providers, NOT the inputs they built the session from (an ARM64 host with the
model present can still end up on CPU here, e.g. when the QNN EP fails to
register).
"""

from __future__ import annotations


def qnn_session(onnx_path, label: str, provider_options: "dict | None" = None):
    """Create an InferenceSession on the Hexagon NPU, CPU fallback on failure.

    provider_options is merged over the default {"backend_path": <htp dll>} —
    e.g. {"htp_performance_mode": "burst"} pins the HTP clock for
    latency-critical models (the pose backend uses it; face ID keeps the
    default power profile it has always run with).
    """
    import onnxruntime as ort

    try:
        import onnxruntime_qnn as qnn

        try:
            ort.register_execution_provider_library(qnn.get_ep_name(), qnn.get_library_path())
        except Exception:
            pass  # already registered from a previous qnn_session() call

        npu_devices = [
            d for d in ort.get_ep_devices()
            if d.ep_name == qnn.get_ep_name() and d.device.type == ort.OrtHardwareDeviceType.NPU
        ]
        if not npu_devices:
            raise RuntimeError("no QNN NPU device found")

        options = {"backend_path": qnn.get_qnn_htp_path()}
        if provider_options:
            options.update(provider_options)

        so = ort.SessionOptions()
        so.add_provider_for_devices(npu_devices, options)
        session = ort.InferenceSession(str(onnx_path), sess_options=so)
        print(f"  {label} running on: {session.get_providers()[0]}")
        return session
    except Exception as e:
        print(f"  [!] QNNExecutionProvider unavailable for {label} ({e}), falling back to CPU ONNX")
        return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


def session_mode(session) -> str:
    """'npu' if the session actually resolved to the QNN EP, else 'cpu'.

    This is the only reliable way to detect qnn_session()'s silent CPU
    fallback — inspect what the session ended up with, not what was asked for.
    """
    try:
        providers = session.get_providers() or []
        return "npu" if providers and "QNN" in providers[0] else "cpu"
    except Exception:
        return "cpu"
