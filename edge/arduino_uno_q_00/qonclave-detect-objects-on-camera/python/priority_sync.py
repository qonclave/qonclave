# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
priority_sync.py -- keeps an edge-side copy of the hub's known-person
priority map (GET /user/known-person-priorities), for the follow-target
selector.

A dedicated daemon-thread client, modeled on main.py's _monitor_hub_health():
fetch immediately on start(), then refresh every refresh_sec. On ANY failure
(hub down, HTTP error, malformed body) the last successfully fetched map is
kept, so following keeps working from cached priorities while the hub is
unavailable. main.py also calls refresh_now() when the hub health monitor
sees the hub come back online, so a dashboard edit made while the edge was
disconnected lands promptly instead of waiting out the interval.
"""

from __future__ import annotations

import threading
import time

import requests


class PriorityMapClient:
    def __init__(self, get_hub_base_url, refresh_sec: float = 15.0,
                 timeout_sec: float = 3.0, logger=None):
        self._get_hub_base_url = get_hub_base_url
        self.refresh_sec = refresh_sec
        self.timeout_sec = timeout_sec
        self._log = logger
        self._lock = threading.Lock()
        self._map: dict[str, int] = {}

    def snapshot(self) -> dict:
        """The current {identity slug: priority} map -- a copy, safe to hand
        to the selector. Empty until the first successful fetch."""
        with self._lock:
            return dict(self._map)

    def refresh_now(self) -> bool:
        """One synchronous fetch. True on success (map replaced), False on
        any failure (last good map kept)."""
        try:
            url = f"{self._get_hub_base_url()}/user/known-person-priorities"
            resp = requests.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            people = resp.json().get("people") or []
            new_map = {}
            for person in people:
                try:
                    identity = person["identity"]
                    priority = int(person["priority"])
                except (TypeError, KeyError, ValueError):
                    continue  # skip malformed entries, keep the rest
                if isinstance(identity, str) and identity:
                    new_map[identity] = priority
        except Exception as e:
            if self._log:
                self._log.debug(f"Priority map refresh failed (keeping last map): {e}")
            return False

        with self._lock:
            changed = new_map != self._map
            self._map = new_map
        if changed and self._log:
            self._log.info(f"Priority map updated: {new_map}")
        return True

    def start(self) -> None:
        """Fetch immediately, then keep refreshing on a daemon thread."""
        threading.Thread(target=self._run, name="PriorityMapSync",
                         daemon=True).start()

    def _run(self) -> None:
        while True:
            self.refresh_now()
            time.sleep(self.refresh_sec)
