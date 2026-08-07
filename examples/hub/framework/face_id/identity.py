"""
identity.py — conditional face identification for the Qonclave framework.

Wraps face_pipeline.py (MediaPipe detection + CavaFace embedding) behind the
same lazy-load, always-returns-a-dict contract as framework/vlm.py: the heavy
model-loading imports (torch, qai_hub_models, onnxruntime-qnn, mediapipe) only
happen inside face_pipeline's functions, never at module import time, so this
module itself is safe to import on any hub machine. identify() reports
{"available": False, ...} wherever those dependencies (or an exported model /
the known_faces database) aren't present, instead of raising.

Unlike the VLM (Snapdragon-only via geniex), face_pipeline's CPU path runs on
any platform (see its own docstring) — this uses the Hexagon NPU only when on
ARM64 with the exported ONNX models present, and otherwise still works via
the CPU (PyTorch) path rather than reporting unavailable.

Public API:
    face_id = FaceIdentityBackend()
    face_id.is_available()
    face_id.warmup()
    face_id.status()
    result = face_id.identify(image_path)   # -> dict (single, best face)
    result = face_id.identify_all(image_path)  # -> dict (one entry per face)
    face_id.enroll(name, image_path)        # -> dict; add a known face
    face_id.enroll(name, path, additional=True)  # -> dict; extra photo, same person
    face_id.known_names()                   # -> list[str]; enrolled people
    face_id.photos_for(name)                # -> list[Path]; that person's photos

A person may be enrolled from several photos (different angles), which all
resolve to one identity -- see face_pipeline.identity_for_path for the two
supported layouts. This matters beyond accuracy: posture timers and follow
priorities are keyed by identity, so two files for one person must not read
as two people.
"""

from __future__ import annotations

import logging
import platform
import re
import threading
import time
from pathlib import Path

from . import face_pipeline as fp

log = logging.getLogger("qonclave.face_id")

KNOWN_FACES_DIR = Path(__file__).parent / "known_faces"

ENROLL_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _slugify_name(name: str) -> str:
    """Person name -> safe filename stem, matching the repo's existing
    'mahesh_babu' / 'pawan_kalyan' convention. Also neutralizes path traversal:
    every non-alphanumeric char (incl. '/', '.', '\\') collapses to '_'."""
    return re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip()).strip("_").lower()


def _is_arm64() -> bool:
    m = platform.machine().upper()
    return "ARM64" in m or "AARCH64" in m


class FaceIdentityBackend:
    """Lazily loads the face-ID detector + embedder. Safe to construct on any machine."""

    def __init__(self, known_faces_dir: str | Path = KNOWN_FACES_DIR):
        self.known_faces_dir = Path(known_faces_dir)
        self._detector = None
        self._model = None
        self._use_npu = False
        self._load_error: str | None = None
        self._load_attempted = False
        self._lock = threading.Lock()  # inference + db reload serialized, mirrors VLMBackend

    # --- capability probe ---------------------------------------------------
    def is_available(self) -> bool:
        if self._detector is not None:
            return True
        if self._load_attempted:
            return False
        return self._try_load()

    def status(self) -> dict:
        return {
            "available": self._detector is not None,
            "mode": ("npu" if self._use_npu else "cpu") if self._detector else None,
            "known_faces_dir": str(self.known_faces_dir),
            "load_attempted": self._load_attempted,
            "load_error": self._load_error,
        }

    def warmup(self) -> bool:
        """Eagerly load the model; returns True on success."""
        return self._try_load()

    # --- internal -------------------------------------------------------------
    def _try_load(self) -> bool:
        with self._lock:
            if self._detector is not None:
                return True
            if self._load_attempted:
                return False
            self._load_attempted = True

            # face_pipeline's CavaFace NPU path has no automatic CPU fallback
            # if the exported model is missing (unlike its detector, which
            # already falls back to CPU internally) - so decide once here.
            self._use_npu = _is_arm64() and fp.CAVAFACE_ONNX_PATH.exists()

            try:
                t0 = time.time()
                # Both detector and embedder run on the Hexagon NPU when
                # available. The detector's NPU model is a from-scratch
                # conversion of Google's actual full_range BlazeFace weights
                # (compiled via Qualcomm AI Hub -- see setup/README): the
                # original qai_hub_models catalog export used a different,
                # less accurate "back model" checkpoint that measurably
                # missed/under-scored turned or distant faces in testing.
                self._detector = fp._build_detector(self._use_npu)
                self._model = (
                    fp._build_cavaface_npu() if self._use_npu else fp._build_cavaface_cpu()
                )
                log.info("Face ID models loaded in %.1fs (mode=%s)",
                         time.time() - t0, "npu" if self._use_npu else "cpu")
                return True
            except Exception as e:
                self._load_error = f"model load failed: {e}"
                log.warning("Face ID unavailable: %s", self._load_error)
                self._detector = None
                self._model = None
                return False

    # --- inference ------------------------------------------------------------
    def identify(self, image_path: str, threshold: float = fp.THRESHOLD) -> dict:
        """
        Detect a face in image_path and match it against known_faces_dir.
        Always returns a dict; never raises for the caller.

        {"available": bool, "face_detected": bool|None, "identified": bool|None,
         "name": str|None, "confidence": float|None, "scores": dict|None,
         "mode": "npu"|"cpu"|None, "latency_s": float|None, "error": str|None}
        """
        if not self.is_available():
            return {
                "available": False, "face_detected": None, "identified": None,
                "name": None, "confidence": None, "scores": None, "mode": None,
                "latency_s": None,
                "error": self._load_error or "face ID not available on this hub",
            }

        mode = "npu" if self._use_npu else "cpu"  # accurate now that loading has happened

        with self._lock:
            t0 = time.time()
            try:
                known = fp._load_db(self._detector, self._model, self.known_faces_dir, self._use_npu)
                unknown_emb = fp.get_embedding(self._detector, self._model, image_path, self._use_npu)
            except Exception as e:
                log.exception("Face ID identify() failed")
                return {
                    "available": True, "face_detected": None, "identified": None,
                    "name": None, "confidence": None, "scores": None, "mode": mode,
                    "latency_s": None, "error": f"identify failed: {e}",
                }
            latency = round(time.time() - t0, 3)

        if unknown_emb is None:
            return {
                "available": True, "face_detected": False, "identified": False,
                "name": None, "confidence": None, "scores": None, "mode": mode,
                "latency_s": latency, "error": None,
            }

        if not known:
            return {
                "available": True, "face_detected": True, "identified": False,
                "name": None, "confidence": None, "scores": {}, "mode": mode,
                "latency_s": latency, "error": "no known faces enrolled",
            }

        scores = fp.match_scores(known, unknown_emb)
        best_name = max(scores, key=scores.get)
        best_score = scores[best_name]
        identified = best_score > threshold

        log.info("Face ID identify (%.2fs): %s (%.1f%%)%s", latency, best_name,
                 best_score * 100, "" if identified else " [below threshold]")

        return {
            "available": True, "face_detected": True,
            "identified": identified,
            "name": best_name if identified else None,
            "confidence": round(best_score, 4),
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "mode": mode, "latency_s": latency, "error": None,
        }

    def identify_all(self, image_path: str, threshold: float = fp.THRESHOLD) -> dict:
        """
        Detect *every* face in image_path and match each against known_faces_dir.
        Always returns a dict; never raises for the caller.

        {"available": bool, "face_count": int, "faces": [ {face-result}, ... ],
         "mode": "npu"|"cpu"|None, "latency_s": float|None, "error": str|None}

        Each entry in "faces" mirrors identify()'s per-face shape:
        {"face_detected": True, "identified": bool, "name": str|None,
         "confidence": float|None, "scores": dict, "bbox": (x1,y1,x2,y2),
         "detector_score": float}, ordered highest detector-confidence first.
        """
        if not self.is_available():
            return {
                "available": False, "face_count": 0, "faces": [], "mode": None,
                "latency_s": None,
                "error": self._load_error or "face ID not available on this hub",
            }

        mode = "npu" if self._use_npu else "cpu"

        with self._lock:
            t0 = time.time()
            try:
                known = fp._load_db(self._detector, self._model, self.known_faces_dir, self._use_npu)
                faces = fp.get_embeddings(self._detector, self._model, image_path, self._use_npu)
            except Exception as e:
                log.exception("Face ID identify_all() failed")
                return {
                    "available": True, "face_count": 0, "faces": [], "mode": mode,
                    "latency_s": None, "error": f"identify failed: {e}",
                }
            latency = round(time.time() - t0, 3)

        results = []
        for f in faces:
            emb = f["embedding"]
            if not known:
                results.append({
                    "face_detected": True, "identified": False, "name": None,
                    "confidence": None, "scores": {}, "bbox": f["bbox"],
                    "detector_score": round(f["score"], 4),
                })
                continue
            scores = fp.match_scores(known, emb)
            best_name = max(scores, key=scores.get)
            best_score = scores[best_name]
            identified = best_score > threshold
            results.append({
                "face_detected": True,
                "identified": identified,
                "name": best_name if identified else None,
                "confidence": round(best_score, 4),
                "scores": {k: round(v, 4) for k, v in scores.items()},
                "bbox": f["bbox"],
                "detector_score": round(f["score"], 4),
            })

        n_known = sum(1 for r in results if r["identified"])
        log.info("Face ID identify_all (%.2fs): %d face(s), %d known, %d unknown%s",
                 latency, len(results), n_known, len(results) - n_known,
                 "" if known else " [no known faces enrolled]")

        return {
            "available": True, "face_count": len(results), "faces": results,
            "mode": mode, "latency_s": latency,
            "error": None if known or not results else "no known faces enrolled",
        }

    # --- enrollment -----------------------------------------------------------
    def known_names(self) -> list[str]:
        """People currently enrolled in known_faces_dir.

        Grouped by fp.identity_for_path -- the same rule _load_db() enrolls by,
        so the roster can never disagree with what inference can actually
        match. Several photos of one person report as one name.
        """
        d = self.known_faces_dir
        if not d.is_dir():
            return []
        return sorted({fp.identity_for_path(p, d) for p in d.rglob("*")
                       if p.is_file() and p.suffix.lower() in ENROLL_EXTS})

    def photos_for(self, name: str) -> list[Path]:
        """Every enrolled photo belonging to one person, oldest filename first."""
        d = self.known_faces_dir
        if not d.is_dir():
            return []
        slug = _slugify_name(name)
        return sorted(p for p in d.rglob("*")
                      if p.is_file() and p.suffix.lower() in ENROLL_EXTS
                      and fp.identity_for_path(p, d) == slug)

    def _invalidate_db_cache(self):
        """Drop the cached embeddings so the next identify() rebuilds the DB
        and picks up a freshly enrolled face. _load_db() already rebuilds when
        an image is newer than the cache, but deleting is robust to clock skew
        and same-second writes."""
        for use_npu in (True, False):
            cache = fp._cache_path(self.known_faces_dir, use_npu)
            try:
                cache.unlink(missing_ok=True)
            except OSError as e:
                log.warning("could not remove embeddings cache %s: %s", cache, e)

    def enroll(self, name: str, image_path: str, additional: bool = False) -> dict:
        """
        Add a person to known_faces_dir from an uploaded image so subsequent
        inference recognizes them. Validates that a face is actually detectable
        (when the model is available) before saving, then invalidates the
        embeddings cache. Always returns a dict; never raises for the caller.

        additional=False (default) replaces this person's existing photo.
        additional=True keeps it and files the new one as ``slug__N.ext``, so
        one person can be enrolled from several angles -- recognition takes the
        best match across them, so an extra photo can only help.

        {"ok": bool, "name": str|None, "slug": str|None, "path": str|None,
         "replaced": bool, "photo_count": int, "error": str|None}
        """
        slug = _slugify_name(name)
        if not slug:
            return {"ok": False, "name": None, "slug": None, "path": None,
                    "replaced": False, "error": "empty or invalid name"}

        src = Path(image_path)
        ext = src.suffix.lower()
        if ext not in ENROLL_EXTS:
            return {"ok": False, "name": name, "slug": slug, "path": None,
                    "replaced": False,
                    "error": f"unsupported image type '{ext or '(none)'}'; "
                             f"use one of {sorted(ENROLL_EXTS)}"}

        # Reject a photo with no detectable face up front, so we don't enroll a
        # name that can never match. Only possible when the model loaded; if it
        # didn't (e.g. deps missing), we still save the file so enrollment works
        # on hubs where face-ID isn't running.
        if self.is_available():
            try:
                from PIL import Image
                with self._lock:
                    faces = fp.detect_faces(self._detector, Image.open(image_path))
            except Exception as e:
                log.exception("enroll() face check failed")
                return {"ok": False, "name": name, "slug": slug, "path": None,
                        "replaced": False, "error": f"face check failed: {e}"}
            if not faces:
                return {"ok": False, "name": name, "slug": slug, "path": None,
                        "replaced": False,
                        "error": "no face detected in the uploaded photo; "
                                 "use a clear, front-facing image"}

        self.known_faces_dir.mkdir(parents=True, exist_ok=True)
        existing_photos = self.photos_for(slug)

        replaced = False
        if additional:
            dest = self._next_photo_path(slug, ext, existing_photos)
        else:
            # Replace every photo this person already has, whatever extension
            # or index, so no stale image survives a re-enroll.
            for old in existing_photos:
                try:
                    old.unlink()
                    replaced = True
                except OSError as e:
                    return {"ok": False, "name": name, "slug": slug, "path": None,
                            "replaced": False, "photo_count": len(existing_photos),
                            "error": f"could not replace existing photo: {e}"}
            dest = self.known_faces_dir / f"{slug}{ext}"

        try:
            with open(src, "rb") as fin, open(dest, "wb") as fout:
                fout.write(fin.read())
        except OSError as e:
            log.exception("enroll() save failed")
            return {"ok": False, "name": name, "slug": slug, "path": None,
                    "replaced": replaced, "photo_count": len(self.photos_for(slug)),
                    "error": f"could not save photo: {e}"}

        self._invalidate_db_cache()
        count = len(self.photos_for(slug))
        log.info("Enrolled known face: %s -> %s (%d photo(s))%s", name, dest.name,
                 count, " (replaced existing)" if replaced else "")
        return {"ok": True, "name": name, "slug": slug, "path": str(dest),
                "replaced": replaced, "photo_count": count, "error": None}

    def _next_photo_path(self, slug: str, ext: str, existing: list[Path]) -> Path:
        """``slug__N`` for the lowest free N, so adding a photo never silently
        overwrites one already enrolled under a different extension."""
        taken = {p.stem for p in existing}
        if slug not in taken:
            return self.known_faces_dir / f"{slug}{ext}"
        n = 2
        while f"{slug}{fp.PHOTO_SEPARATOR}{n}" in taken:
            n += 1
        return self.known_faces_dir / f"{slug}{fp.PHOTO_SEPARATOR}{n}{ext}"

    def close(self):
        with self._lock:
            self._detector = None
            self._model = None
