"""
known_person_priorities.py — hub-side storage for known-person follow
priorities (1 = highest; lower numbers win). Security-app-specific by design:
the framework stays use-case agnostic, and framework/server.py exposes this
only through getattr-hooks on the policy (see /user/known-person-priorities).

Keyed by the face-enrollment slug (framework/face_id/identity._slugify_name),
so display-name vs. filename differences can never break the lookup. People
enrolled without an explicit record default to priority 100; stored entries
for people no longer enrolled are ignored (and unknown slugs rejected on
write) rather than deleted, so re-enrolling someone restores their priority.

Persisted format (known_person_priorities.json, gitignored runtime state):
    {"priya": {"priority": 1}}
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("qonclave.hub")

DEFAULT_PRIORITY = 100
DEFAULT_PATH = Path(__file__).parent / "known_person_priorities.json"


class KnownPersonPriorityStore:
    def __init__(self, path: str | Path = DEFAULT_PATH, known_names=None):
        """known_names: zero-arg callable returning the currently enrolled
        slugs (e.g. FaceIdentityBackend.known_names), or None when face ID
        is not enabled on this hub (the roster is then empty)."""
        self.path = Path(path)
        self._known_names = known_names
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        """Tolerant load: missing or corrupt file (or entries) -> skipped,
        never an exception (model: framework/icons.py load_cache)."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        data = {}
        for slug, entry in raw.items():
            if not isinstance(slug, str) or not isinstance(entry, dict):
                continue
            priority = entry.get("priority")
            if isinstance(priority, bool) or not isinstance(priority, int):
                continue
            if priority > 0:
                data[slug] = {"priority": priority}
        return data

    def _enrolled(self) -> list[str]:
        if self._known_names is None:
            return []
        try:
            return list(self._known_names())
        except Exception as e:
            log.warning("Could not list enrolled faces for priorities: %s", e)
            return []

    def list_people(self) -> list[dict]:
        """One entry per ENROLLED person (stored ∩ enrolled; stale stored
        slugs omitted), missing records defaulting to 100, sorted by
        (priority, identity) so the dashboard and edge see a stable order."""
        with self._lock:
            people = [
                {"identity": slug,
                 "priority": self._data.get(slug, {}).get("priority", DEFAULT_PRIORITY)}
                for slug in self._enrolled()
            ]
        people.sort(key=lambda p: (p["priority"], p["identity"]))
        return people

    def set_priority(self, slug: str, priority) -> dict | None:
        """Set one person's priority. Returns {"identity", "priority"} on
        success, None when slug isn't enrolled (route answers 404). Raises
        ValueError for anything but a positive integer (or a string of one)
        — validation style modeled on posture.py update_settings()."""
        if isinstance(priority, bool) or priority is None:
            raise ValueError("priority must be a positive integer")
        if isinstance(priority, str):
            try:
                priority = int(priority.strip())
            except ValueError:
                raise ValueError("priority must be a positive integer")
        if not isinstance(priority, int):
            raise ValueError("priority must be a positive integer")
        if priority <= 0:
            raise ValueError("priority must be greater than zero")

        with self._lock:
            if slug not in self._enrolled():
                return None
            self._data[slug] = {"priority": priority}
            self._save_locked()
        return {"identity": slug, "priority": priority}

    def _save_locked(self) -> None:
        """Atomic persist: write a sibling .tmp, fsync, then os.replace() so
        a crash mid-write can never leave a truncated/corrupt JSON behind."""
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, self.path)
