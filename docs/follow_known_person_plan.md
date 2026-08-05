# Known-Person Priority Following Plan

## Goal

Make the robot prefer recognized people over unknown people. When multiple
known people are visible, select the person with the highest priority stored
on the hub. If the selected known person temporarily disappears, wait for a
configurable number of frames before falling back to an unknown person.

## Target Selection Order

```text
Visible known person with highest hub priority
                    |
                    v
Previously selected known person within grace period
                    |
                    v
Longest-established visible unknown person
                    |
                    v
No target
```

Use lower numbers for higher priority:

```text
1 = highest priority
2 = next priority
3 = next priority
```

## Selection Rules

1. If one or more known people are visible, follow the known person with the
   lowest priority number.
2. If multiple visible known people have equal priority:
   - Keep the current target if that person is among them.
   - Otherwise select the track with the highest `frames_tracked` value.
   - Use `track_id` as the final deterministic tie-breaker.
3. A known person immediately preempts an unknown target.
4. A higher-priority known person immediately preempts a lower-priority known
   person.
5. When the selected known person disappears:
   - Retain that person as the intended target for a grace period.
   - Do not switch to an unknown person during the grace period.
   - If the known track reappears, resume following it without switching.
   - If another known person appears, select among known people by priority.
   - Only after the grace period expires may the robot select an unknown
     person.
6. If no known person has been detected, preserve the existing behavior and
   follow the longest-established visible track.

## Known-Target Grace Period

Add an edge configuration value:

```text
FOLLOW_KNOWN_GRACE_FRAMES=10
```

The target selector retains:

```text
target_track_id
target_identity
target_status
missing_frames
priority
```

Example:

```text
Frame 1: Jogendra is known on track 3 -> follow track 3
Frame 2: only unknown track 7       -> wait; do not switch
Frame 3: only unknown track 7       -> wait
Frame 4: Jogendra track 3 returns   -> resume track 3
```

If Jogendra remains absent beyond ten detection frames:

```text
Frame 11: grace expires -> select the best unknown track
```

During the grace period, do not turn using the missing person's stale
position. Allow an already active short turn to finish, then hold position
until the target returns, another known target is selected, or the grace
period expires.

## Hub-Side Priority Storage

Keep security-specific priority behavior under the security application and
out of `hub/framework/`:

```text
hub/apps/security/known_person_priorities.py
hub/apps/security/known_person_priorities.json
```

Suggested persisted format:

```json
{
  "jogendra": {
    "priority": 1
  },
  "alice": {
    "priority": 2
  },
  "bob": {
    "priority": 3
  }
}
```

Use the normalized face-enrollment slug as the identity key. This prevents
differences between display names and enrolled image filenames from breaking
priority lookup.

Persistence requirements:

- Validate priorities as positive integers.
- Write changes atomically.
- Ignore stale entries for faces that are no longer enrolled.
- Assign newly enrolled people a default priority such as `100`.
- Treat recognized people without an explicit record as priority `100`.
- Permit equal priorities and resolve ties on the edge.

## Hub API

Register security-specific routes from `hub/apps/security/`:

```http
GET /user/known-person-priorities
PUT /user/known-person-priorities/<slug>
```

Example response:

```json
{
  "people": [
    {
      "identity": "jogendra",
      "priority": 1
    },
    {
      "identity": "alice",
      "priority": 2
    }
  ]
}
```

Example update body:

```json
{
  "priority": 1
}
```

Add a priority control beside each enrolled person in the hub dashboard's
known-face roster.

## Edge Priority Synchronization

The edge maintains an in-memory priority map:

```python
{
    "jogendra": 1,
    "alice": 2,
}
```

Synchronization behavior:

- Fetch the map when the edge starts.
- Refresh every 10 to 30 seconds.
- Refresh after reconnecting to the hub.
- Retain the last successful map while the hub is temporarily unavailable.
- Use priority `100` for a recognized identity absent from the map.

Keeping priority synchronization separate from `/track/analyze` leaves the
recognition endpoint use-case agnostic and avoids placing security-specific
behavior in the framework.

## Edge Target Selector

Add:

```text
edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/python/follow_target_selector.py
```

Conceptual interface:

```python
selection = selector.select(
    person_tracks,
    identity_snapshot,
    priority_map,
)
```

Example result:

```python
{
    "track_id": 3,
    "identity": "jogendra",
    "status": "known",
    "priority": 1,
    "reason": "highest_priority_known",
}
```

Replace the current selection:

```python
max(person_tracks, key=lambda track: track["frames_tracked"])
```

with the target selector result. Keep the state machine outside `main.py` so
selection and grace-period transitions can be tested independently.

## Identity Behavior

The existing `IdentityMap` already provides useful stability:

- Once a track becomes known, that identity is sticky.
- A later `unknown` or `no_face` result cannot erase the known name.
- Face sampling stops after a track becomes known.

Therefore, ordinary recognition flicker does not cause switching. The new
grace state handles the separate case where the known track is temporarily
absent from the current frame.

If the tracker deletes the old track and the person later returns with a new
`track_id`, face recognition must confirm the new track before the robot moves
toward it. Retaining the desired identity during the grace period must never
permit movement based on a stale bounding box.

## Motor Behavior During Target Changes

- Unknown to known: select the known target immediately.
- Lower-priority known to higher-priority known: select the higher-priority
  target immediately.
- Known to temporarily absent: hold; do not select an unknown target.
- Known to unknown after grace expiration: permit fallback.

For the first implementation, allow the MCU to finish its current short turn
before issuing a correction for a newly selected target. Once the MCU is idle,
calculate a fresh bearing from the new target's current bounding box. This
avoids rapid motor reversals.

## UI and Logging

Send target state to the edge Web UI:

```json
{
  "track_id": 3,
  "identity": "jogendra",
  "status": "known",
  "priority": 1,
  "state": "following",
  "reason": "highest_priority_known"
}
```

Useful states:

```text
following
known_target_missing
fallback_unknown
no_target
```

Distinguish the selected target in the preview:

```text
Track 3: Jogendra [FOLLOWING, P1]
Track 7: Unknown
```

Log target transitions and grace-period state without logging unchanged state
on every frame:

```text
Follow target changed: unknown track 7 -> Jogendra track 3 (known priority 1)
Holding known target Jogendra: missing 3/10 frames
Known-target grace expired: falling back to unknown track 7
```

## Tests

Add unit and integration coverage for:

1. One known and several unknown people: the known person wins.
2. Several known people: the lowest priority number wins.
3. Equal known priorities: the current target remains selected.
4. No known people: the longest-established unknown track wins.
5. A known target disappears for fewer than the grace frames: do not switch
   to an unknown track.
6. The known target returns during the grace period: resume that target.
7. The known target remains absent past the grace period: begin unknown
   fallback.
8. Another known person appears during the grace period: select among known
   people by priority.
9. A higher-priority known person appears while following another known
   person: preempt the lower-priority target.
10. The priority API is unavailable: cached or default priorities continue to
    work.
11. A track is deleted and recreated: require identity confirmation on the new
    track.
12. No target is visible during the grace period: do not generate a stale
    motor command.
13. Priority API validation, persistence, defaults, and atomic updates work.
14. Dashboard priority changes appear on the edge after synchronization.

## Expected Change Scope

This feature should add:

- A priority store and API under `hub/apps/security/`.
- Priority controls in the security dashboard.
- Edge-side priority synchronization.
- A testable edge target-selection state machine.
- Target status in the edge UI and logs.
- Unit and integration tests for priority and grace behavior.

It should not change:

- Face embedding or recognition matching.
- The MCU motor controller.
- The existing identity upgrade rules.
- Generic framework policy behavior.
