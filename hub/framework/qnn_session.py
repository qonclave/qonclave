"""
qnn_session.py — the one place this repo builds a Hexagon NPU inference session.

Lifted out of `face_id/face_pipeline.py` so face ID and pose share a single QNN
entry point rather than two copies that drift. `face_pipeline._qnn_session`
remains as a thin alias so its CLI and any external caller keep working.

The trap this function exists to avoid is documented below and is worth reading
before touching it: the failure mode is not an error, it is silent slowness.
"""

from __future__ import annotations

from pathlib import Path


def qnn_session(onnx_path: Path, label: str, *, quiet: bool = False):
    """Create an InferenceSession on the Hexagon NPU via onnxruntime-qnn.

    onnxruntime's QNN support is a dynamically-registered "plugin" execution
    provider (the device-based EP API added in 1.20+): the provider library must
    be registered by path, then bound to the actual NPU OrtEpDevice through
    SessionOptions.add_provider_for_devices.

    Passing "QNNExecutionProvider" as a plain string to
    InferenceSession(providers=...) **silently no-ops and runs on CPU** on this
    onnxruntime version. That is the whole reason this helper exists.

    Falls back to CPUExecutionProvider on any failure, because a hub that runs
    slowly is better than a hub that does not start. The cost of that choice is
    that a missing model or an unregistered EP looks like "working, just slow" —
    roughly 45 ms instead of 1.4 ms for pose. Callers MUST surface the resolved
    provider in their `status()` so the difference is visible; see
    `resolved_mode()`.
    """
    import onnxruntime as ort

    def _say(msg: str) -> None:
        if not quiet:
            print(msg)

    try:
        import onnxruntime_qnn as qnn

        try:
            ort.register_execution_provider_library(qnn.get_ep_name(), qnn.get_library_path())
        except Exception:
            pass  # already registered by a previous call

        npu_devices = [
            d for d in ort.get_ep_devices()
            if d.ep_name == qnn.get_ep_name() and d.device.type == ort.OrtHardwareDeviceType.NPU
        ]
        if not npu_devices:
            raise RuntimeError("no QNN NPU device found")

        so = ort.SessionOptions()
        so.add_provider_for_devices(npu_devices, {"backend_path": qnn.get_qnn_htp_path()})
        session = ort.InferenceSession(str(onnx_path), sess_options=so)
        _say(f"  {label} running on: {session.get_providers()[0]}")
        return session
    except Exception as e:
        _say(f"  [!] QNNExecutionProvider unavailable for {label} ({e}), falling back to CPU ONNX")
        return ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])


def resolved_mode(session) -> str:
    """"npu" or "cpu", from what the session actually bound to.

    Read this rather than assuming: `qnn_session` degrades silently by design,
    so the only honest source of truth is the session itself.
    """
    try:
        providers = session.get_providers()
    except Exception:
        return "unknown"
    if not providers:
        return "unknown"
    return "cpu" if providers[0] == "CPUExecutionProvider" else "npu"
