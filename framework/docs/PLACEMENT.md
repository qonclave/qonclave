# Tiered AI Placement

Any node runs local AI if it can. **Where a given inference runs is a per-request decision**, not
a property of where code happens to be deployed.

This generalizes something the reference implementation already does. The UNO Q runs a local
person detector and escalates a frame to the hub for VLM verification when its confidence crosses
a threshold — a two-rung ladder with one hardcoded metric. `ARCHITECTURE.md` already names the
rungs: *"Triage (Tier 1 AI)"* on the edge, *"Heavy Lifting (Tier 2+ AI)"* on compute. Placement
makes that ladder explicit, multi-metric, and three-rung.

---

## Declared intent vs. measured fact

The split the whole layer rests on:

| The **application** declares (per task) | The **framework** measures (per node) |
|---|---|
| `complexity` — model class needed | `power` — battery %, on-mains, thermal headroom |
| `urgency` / `deadline_ms` | `load` — CPU %, active tasks, queue depth |
| `privacy` — may this leave the device or subnet? | `capabilities` — what this node can actually run |
| `use_case` — free-form tag | `rtt_ms` — measured latency to each candidate |

A developer never writes *"run this on the hub."* They write the conditions, against facts they
did not have to collect.

Keeping these apart is also what makes a surprising placement debuggable: either the measurement
was wrong or the decision was, and those are different bugs in different files.

---

## The developer surface

```python
from qonclave.placement import PlacementPolicy, Placement, Tier
from qonclave.core.enums import Complexity, Privacy

class MyPlacement(PlacementPolicy):
    def decide(self, task, tiers):
        d = task.descriptor

        if d.privacy is Privacy.NO_EGRESS:              # never a shared multi-tenant node
            return Placement(Tier.HUB, deny=[Tier.COMPUTE])

        if d.deadline_ms and d.deadline_ms < 50:        # a hop alone blows the budget
            return Placement(Tier.EDGE, on_miss="degrade")

        me = tiers.local
        if me.power.battery_pct and me.power.battery_pct < 20 and not me.power.on_mains:
            return Placement(Tier.HUB)                  # don't spin the local NPU

        if d.complexity >= Complexity.VLM_REASON:
            return Placement(Tier.COMPUTE, fallback=[Tier.HUB, Tier.EDGE])

        return Placement(Tier.EDGE)
```

There is **no rule DSL and no ruleset file**. The decision is code, in the same idiom as
`hub.Policy`, so there is one thing to learn rather than two.

`DefaultPlacement` ships with the framework and handles the cases that are nearly always right, so
an application that has not thought about placement yet gets something defensible rather than
something arbitrary.

### `Placement` fields

| | |
|---|---|
| `tier` | where you want it to run |
| `fallback` | tiers to try, in order, if that one is unreachable or cannot serve |
| `deny` | tiers this task must never reach |
| `on_miss` | `fail` \| `degrade` (smaller local model) \| `defer` (spool and retry) |
| `prefer` | `"peer"` or `"home"` — a hint among candidates at the chosen tier |
| `reason` | free text, surfaced by `placement-explain` |

An empty `fallback` is a real choice, not an oversight: running somewhere else can be worse than
not running at all.

---

## What the framework owns

| Framework | Developer |
|---|---|
| measuring power, thermal, load, RTT | the decision |
| pruning candidates that cannot serve the complexity | the thresholds |
| walking the fallback chain | which metrics matter |
| **enforcing `deny`** | |
| deducting the deadline across hops | |
| dispatching to the resolved backend | |

### `deny` is enforced, not trusted

A policy that returns `Tier.COMPUTE` for a `no_egress` task is **overridden**. `SECURITY.md` §2
states tenant isolation as a guarantee, and a guarantee that depends on every application author
remembering it is not a guarantee.

The enforcement is deliberately not over-strict: `NO_EGRESS` denies *shared multi-tenant* compute,
not all compute. A dedicated single-tenant node is not an egress risk, and blanket-denying it
would push work back to the hub for no privacy gain — the kind of rule that gets switched off in
production and takes the real protection with it.

---

## The deadline travels on the wire

An edge that escalates a task with a 200 ms budget after spending 140 ms locally has left the hub
60 ms — and the hub cannot know that unless it is told.

```python
descriptor = task.for_escalation("edge")   # deducts elapsed, appends the hop
```

`remaining_ms` is on `edge-event.schema.json` for exactly this. Skip it and every tier re-plans
against the original deadline, which fails silently and only surfaces as missed SLAs under load.

---

## Two escalation modes

- **Proactive** — decide up front, dispatch straight to the chosen tier.
- **Cascade** — run here, escalate on low confidence or an inconclusive result. This is the
  existing edge→hub behavior; the threshold just moves out of firmware and into a policy.

---

## Peer hubs are candidates, not a new rung

An edge holding a valid capability grant for a foreign hub makes that hub an additional candidate
**at the HUB tier**. `TierState` already carries per-node load, latency, and capability, so
nothing about the ladder changes.

```python
if tiers.hub and tiers.hub.load.cpu_percent > 90:
    return Placement(Tier.HUB, prefer="peer")
```

**Placement decides which tier; the grant decides which instances of that tier are permitted.**
The framework filters the candidate set to grant-holders before `decide()` ever runs, so placement
code never has to know about certificates.

`prefer` is a hint, not a constraint. If a policy asks for a peer and none is authorized, the home
hub is still correct — treating the hint as binding would turn a load optimization into an outage.

---

## On constrained devices

`placement.h` exposes the same shape as a C callback handed a populated `qc_tier_set_t`. Dropping
the rule DSL is what made that possible: no evaluator, no parser, no config file on the device.

On a daily-wake sensor the decision is usually a constant — triage here, escalate the rest — and
that is fine. The value of having it at all is that the decision sits in one auditable place with
the same signature as on the hub, rather than as thresholds scattered through firmware.

---

## Debugging

```bash
qonclave placement-explain --task fixtures/task_vlm.json --battery 15
```

Prints the measured `TierSet` the policy was handed and the `Placement` it returned. Without it,
debugging placement means guessing at battery, thermal, and RTT values that were true for one
request and gone by the next.
