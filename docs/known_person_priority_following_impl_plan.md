# Known-Person Priority Following

## Context

The robot currently follows whichever person track has the highest `frames_tracked`
(`main.py:764`), ignoring identity. This change makes it prefer recognized people:
follow the visible known person with the lowest hub-stored priority number
(1 = highest), hold for a grace period (`FOLLOW_KNOWN_GRACE_FRAMES=10`) when that
person briefly disappears instead of chasing an unknown, and only fall back to the
longest-established unknown track after the grace expires. Priorities live on the
hub (per enrolled face slug), are editable from the dashboard roster, and sync to
the edge periodically. This implements the spec in `docs/follow_known_person_plan.md`.

All edge paths below are under
`edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/`.

## Key design decisions

- **Selector owns all grace state; motor commands only from current-frame tracks.**
  `FollowTargetSelector.select()` returns a dict whose `"track"` key is either a
  track from this frame's `person_tracks` or `None` (during grace / no target).
  `main.py` computes bearings/turns only from `selection["track"]`, so a stale
  bounding box structurally cannot produce a turn (spec case 12).
- **IdentityMap pruning hazard**: detection runs ~1.5 Hz, so 10 grace frames
  ≈ 6.7 s > IdentityMap's 5 s `inactive_grace_sec`. The selector retains its own
  copy of `target_identity`/`priority`; if the *same* `track_id` reappears
  mid-grace after its identity entry was pruned, resume from the retained copy.
  Do NOT change `inactive_grace_sec` or any `identity_map.py` rules.
- **Track recreated with a new id** requires recognition to re-confirm: a new
  track only becomes a known candidate once `identity_snapshot[tid]["status"] ==
  "known"` — falls out naturally, no extra machinery (spec case 11).
- **Priority sync**: dedicated daemon-thread client (`priority_sync.py`) modeled
  on `_monitor_hub_health()` (`main.py:384-398`), default 15 s refresh, injected
  `get_hub_base_url` callable (same pattern as `AnalysisClient`). Keeps last good
  map on failure; explicit `refresh_now()` on hub reconnect (False→True flip in
  `_monitor_hub_health`).
- **Hub wiring via getattr-hooks** (precedent: `/user/investigation`,
  `framework/server.py:647-667`) — `framework/policy.py` stays untouched; all
  logic in `hub/apps/security/`. Routes 404 when the policy lacks the hooks.
- **`person_centering.py` unchanged**: its global cooldown + the
  `robot_motion_active` gate already implement "let the current short turn
  finish, then fresh bearing". Resetting the cooldown on preemption would cause
  the rapid reversals the spec forbids.
- **`known_person_priorities.json` is gitignored** (per-deployment runtime state,
  like `hub/icons_cache.json`).
- **UI**: new `follow_status` socket event (leave `person_tracking_status`
  untouched); emit per callback, log transitions only.

## Part A — Edge

### A1. `python/follow_target_selector.py` (new, stdlib-only)

```python
DEFAULT_PRIORITY = 100
FOLLOWING = "following"; KNOWN_TARGET_MISSING = "known_target_missing"
FALLBACK_UNKNOWN = "fallback_unknown"; NO_TARGET = "no_target"

class FollowTargetSelector:
    def __init__(self, grace_frames: int = 10): ...
    def select(self, person_tracks, identity_snapshot, priority_map) -> dict:
        # {"track_id", "identity", "status", "priority", "state", "reason",
        #  "missing_frames", "grace_frames", "track": dict|None}
```

Algorithm order inside `select()` (one call per detection callback = one grace tick):
1. Known candidates = visible tracks with snapshot status `known`;
   `prio = priority_map.get(entry["name"], DEFAULT_PRIORITY)` (hub identity IS
   the slug). Plus the same-id-resume synthetic candidate from retained state
   (see hazard above).
2. If any: pick `min` by `(priority, 0 if tid == current_target else 1,
   -frames_tracked, track_id)` — encodes rules 1-2 (stickiness, frames, id).
   Reset `missing_frames`; state `following`; reasons: `highest_priority_known` /
   `kept_current_equal_priority` / `preempted_unknown` /
   `preempted_lower_priority` / `target_reacquired`.
3. Elif retained known target and `missing_frames < grace_frames`: increment,
   return `known_target_missing` with retained id/identity/priority,
   `track=None`, reason `grace_hold`. (Runs even when unknowns are visible.)
4. Else clear retained known target; if tracks visible: unknown fallback by max
   `frames_tracked` (tie: min track_id), state `fallback_unknown`,
   `priority=None`, reason `grace_expired_fallback` on the expiry frame else
   `longest_established_unknown`. Unknown targets get no grace.
5. Else `no_target`.

No logging/threading in the module (detection-callback thread only).

### A2. `test_follow_target_selector.py` (new, app root)

`run_all()` + `__main__` convention (model: `test_person_centering.py`,
`test_identity_map.py`). Helpers `_track(tid, frames)`, `_known(name)`,
`_unknown()`. Cases → spec: known beats unknown (1); lowest priority wins (2);
equal-priority keeps current + frames/track_id tie-breaks (3); unknown fallback
(4); grace holds 1..9 with unknown visible, `track is None` (5, 12); resume
during grace incl. pruned-snapshot same-id variant (6); expiry exactly at
grace+1 (7); other known appears mid-grace (8); preemption known→known and
unknown→known (9); missing identity defaults to 100 (10); recreated track needs
confirmation then follows once known (11); empty frame still ticks grace (12).

### A3. `python/priority_sync.py` + `test_priority_sync.py` (new)

```python
class PriorityMapClient:
    def __init__(self, get_hub_base_url, refresh_sec=15.0, timeout_sec=3.0, logger=None)
    def snapshot(self) -> dict[str, int]   # lock-guarded copy; {} before first success
    def refresh_now(self) -> bool          # GET {base}/user/known-person-priorities
    def start(self) -> None                # daemon thread: immediate fetch, then loop
```

Parses `{"people": [{"identity", "priority"}]}`; skips malformed entries; on any
error keeps the last map and returns False. INFO log on map change, DEBUG on
failure. Tests mock `requests.get` (model: `test_analysis_client.py`): success,
malformed JSON, HTTP 500/exception keep old map (case 10), updated payload
replaces map (edge half of case 14).

### A4. Integrate into `python/main.py`

- Env block near line 502: `FOLLOW_KNOWN_GRACE_FRAMES` (10),
  `FOLLOW_PRIORITY_REFRESH_SEC` (15), `FOLLOW_PRIORITY_TIMEOUT_SEC` (3).
- Singletons near `identity_map = IdentityMap()`: `follow_selector`,
  `priority_client` (+ `.start()`).
- `_monitor_hub_health()`: capture `was_online`; on False→True spawn
  `priority_client.refresh_now` in a daemon thread.
- In `send_detections_to_ui()`:
  - Build `identity_snapshot` unconditionally (move out of `if person_tracks:`
    at line 736; empty dict when no tracks). Keep existing identity_map
    logging/emit gated as today.
  - `selection = follow_selector.select(person_tracks, identity_snapshot,
    priority_client.snapshot())` — unconditional.
  - New `_log_follow_state_if_changed(selection)` helper (model:
    `_log_identity_map_if_changed`, line 597): compares
    `(state, track_id, identity, priority, missing_frames)`; produces the
    spec's transition lines (`Follow target changed: ...`,
    `Holding known target X: missing n/10 frames`,
    `Known-target grace expired: ...`).
  - `ui.send_message("follow_status", ...)` with all selection keys except
    `"track"`.
  - Overlay labels (748-752): for the selected visible track, append
    ` [FOLLOWING, P{prio}]` (no `, P…` for unknown fallback). Store the
    selected id in a new `_overlay_target_id` (under `_overlay_state_lock`).
  - Replace line 764's `tracked = max(...)` with `target_track =
    selection["track"]`; bearing + `person_tracking_status` + turn block
    (767-806) run only `if target_track is not None`. LED bitmap: use
    `target_track`, else fall back to max-frames track for display only;
    empty-tracks branches unchanged.
- No changes to `identity_map.py`, `person_centering.py`, `person_tracker.py`,
  `analysis_client.py`, or sketch/MCU code.

### A5. `python/track_overlay.py` highlight

Add `highlight_track_id=None` kwarg to `_draw_tracks` / `draw_track_overlay` /
`draw_track_overlay_bgr`; green `_HIGHLIGHT_COLOR = (80, 220, 80)` for the
selected track's box/label. Pass `_overlay_target_id` from
`_preview_publisher()`. One new test in `test_track_overlay.py`; existing calls
unchanged by default.

### A6. Edge web UI

`assets/index.html`: a `#followStatus` line near the detections panel.
`assets/app.js`: handler for `follow_status` rendering
`Following: Priya (Track 3, P1)` / `Holding for Priya (3/10 frames)` /
`Following unknown (Track 7)` / `No target` — via `textContent`.

### A7. Config docs

`.env.example`: new `# --- Known-person following ---` section (3 vars).
App `README.md`: config-table rows + a short selection-order/grace paragraph.

## Part B — Hub

### B1. `hub/apps/security/known_person_priorities.py` (new)

```python
DEFAULT_PRIORITY = 100
DEFAULT_PATH = Path(__file__).parent / "known_person_priorities.json"

class KnownPersonPriorityStore:
    def __init__(self, path=DEFAULT_PATH, known_names=None)  # injectable for tests
    def list_people(self) -> list[dict]   # enrolled slugs, stored ∩ enrolled,
                                          # missing → 100, sorted (priority, identity)
    def set_priority(self, slug, priority) -> dict | None
        # bool/0/negative/non-int → ValueError; un-enrolled slug → None (→404);
        # persist atomically under threading.Lock
```

Persisted format: `{"priya": {"priority": 1}}`. Tolerant `_load()` (missing/
corrupt → `{}`, model: `framework/icons.py`). Atomic save: write `path.tmp`,
`flush()` + `os.fsync()`, `os.replace()` — first atomic writer in hub/.
Validation style model: `apps/security/posture.py` `update_settings()` (line 84).

### B2. `hub/apps/security/policy.py`

In `__init__` (after `self.face_id = face_id`, line 76):
`self.person_priorities = KnownPersonPriorityStore(known_names=(self.face_id.known_names if self.face_id else None))`.
Two hook methods next to `track_settings` (121-125):
`known_person_priorities()` → `list_people()`;
`update_known_person_priority(slug, priority)` → `set_priority(...)`.
No changes to `hub/server.py` or `hub/framework/policy.py`.

### B3. `hub/framework/server.py` — two app-agnostic routes

Next to the `/user/known_faces` block (~line 852), getattr-hook pattern; reuse
`_slugify_name` from `framework/face_id/identity.py:46` on the PUT path param
(also neutralizes traversal):

- `GET /user/known-person-priorities` → 404 if hook absent, else
  `{"people": fn()}`.
- `PUT /user/known-person-priorities/<slug>` (first PUT route — fine in Flask)
  → 404 if hook absent; `ValueError`/`TypeError` → 400; `None` result → 404
  "person not enrolled"; else `{"ok": true, "identity", "priority"}`.
- Add both to the module docstring route list.

### B4. `hub/apps/security/static/dashboard.html`

Rework `loadKnownFaces()` (line 392): fetch `/user/known-person-priorities`;
render per person: slug + `<input type="number" min="1" step="1">` + Set button
(markup model: posture inputs, lines 181-185); fall back to the current
`/user/known_faces` pill rendering on 404. PUT sender modeled on the posture
saver (lines 787-801); refresh roster on success. Render slugs only (innerHTML
safety). Keep `enroll()`'s existing refresh — new people appear at 100.

### B5. `hub/tests/test_known_person_priorities.py` (new) — spec case 13

Store half (tempfile path, injected `known_names`): defaults/missing → 100;
validation rejects 0, -1, "abc", 1.5, True, None, accepts 1, "2"; persistence
across instances; atomicity (no leftover `.tmp`, `os.replace` used); stale
filtering (list omits, set returns None); equal priorities allowed.
Route half (model: `test_track_analyze_endpoint.py` stubs + `test_client()`):
GET shape/sort, PUT persists, bad body → 400, unknown slug → 404, hook-less
policy → 404 on both.

### B6. `.gitignore`

Add `hub/apps/security/known_person_priorities.json` near the hub runtime-state
entries.

## Verification

1. Edge unit tests (repo root, no hardware/cv2 needed for the new ones):
   `python edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/test_follow_target_selector.py`
   and `...test_priority_sync.py`; regression: `test_person_centering.py`,
   `test_identity_map.py`, `test_track_overlay.py`, `test_person_tracker.py`.
2. Hub tests: `python hub/tests/test_known_person_priorities.py`; regression
   `python hub/tests/test_track_analyze_endpoint.py`.
3. API smoke against a running hub (run with
   `C:\Users\qc_de\Qonclave\hub\geniex-env` python): GET roster; PUT
   `{"priority":1}` for priya; 400 on `{"priority":0}`; 404 on `/nobody`;
   inspect the JSON file; hand-add a stale slug and confirm GET omits it.
4. End-to-end (cases 1, 5-7, 9, 14): dashboard sets Priya P1 → edge logs
   `Priority map updated` within ~15 s; known + unknown in frame → preview shows
   `Track N: Priya [FOLLOWING, P1]` (green box) + UI panel; known steps out →
   `Holding known target ... missing n/10 frames`, no turns while only unknown
   visible; return before 10 frames → resume; stay out → grace-expired fallback.
   Kill hub → cached map persists; restart → reconnect refresh fires.
   Edge deploy per the usual workflow: push to GitHub, `git pull` on the board,
   `arduino-app-cli app restart`.

## Out of scope (spec "should not change")

Face embedding/matching, MCU sketch/motor controller, `identity_map.py` rules
and `inactive_grace_sec`, `framework/policy.py`, `person_centering.py`.

Suggested commit order: A1+A2 → A3 → B1+B5(store) → B2+B3+B5(routes) → B4 →
A4+A5+A6+A7 → B6 — each step leaves both suites green.
