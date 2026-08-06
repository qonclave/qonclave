"""
cli.py — developer tooling.

`placement-explain` is the one that earns its place. When an event lands on a surprising tier, the
developer needs to see the measured TierSet their `decide()` was handed and the Placement it
returned. Without it, debugging placement means guessing at battery, thermal, and RTT values that
were true for one request and gone by the next.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

from .core import models
from .core.codec import encode_cbor, encode_json

_MODELS = {
    "EdgeEvent": models.EdgeEvent,
    "Command": models.Command,
    "NodeManifest": models.NodeManifest,
    "CheckinRequest": models.CheckinRequest,
    "CheckinResponse": models.CheckinResponse,
    "CapabilityGrant": models.CapabilityGrant,
    "ArchiveRecord": models.ArchiveRecord,
}


def _spec_root() -> pathlib.Path:
    """framework/spec/v1, located relative to this file inside the repo checkout."""
    return pathlib.Path(__file__).resolve().parents[4] / "spec" / "v1"


def cmd_spec_validate(args) -> int:
    root = pathlib.Path(args.spec) if args.spec else _spec_root()
    schemas = sorted((root / "json-schema").glob("*.schema.json"))
    if not schemas:
        print(f"no schemas under {root / 'json-schema'}", file=sys.stderr)
        return 1

    failed = 0
    for path in schemas:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"FAIL {path.name}: {exc}")
            failed += 1
            continue

        if "$id" not in schema:
            print(f"FAIL {path.name}: no $id")
            failed += 1
        elif not schema["$id"].endswith(path.name):
            # A mismatched $id breaks cross-schema $ref resolution, and does so only in other
            # people's validators — worth failing loudly here.
            print(f"FAIL {path.name}: $id {schema['$id']} does not match filename")
            failed += 1
        else:
            print(f"ok   {path.name}")

    print(f"\n{len(schemas) - failed}/{len(schemas)} schemas valid")
    return 1 if failed else 0


def cmd_placement_explain(args) -> int:
    from .placement import DefaultPlacement, InferenceTask, Tier, TierSet
    from .placement.ladder import PlacementDeferred, PlacementError, resolve
    from .placement.probe import local_state
    from .placement.tiers import TierState

    raw = json.loads(pathlib.Path(args.task).read_text(encoding="utf-8"))
    descriptor = models.TaskDescriptor.model_validate(raw.get("task", raw))

    task = InferenceTask(task_id=raw.get("task_id", "explain"), descriptor=descriptor)

    local = local_state(args.node_id, Tier.EDGE)
    if args.battery is not None:
        local.power = models.Power(battery_pct=float(args.battery), on_mains=False)

    candidates: list[TierState] = [local]
    if not args.no_hub:
        candidates.append(
            TierState(tier=Tier.HUB, node_id="hub-alpha", rtt_ms=5.0,
                      capabilities=models.Capabilities(max_complexity=models.Complexity.VLM_REASON))
        )
    if not args.no_compute_peer:
        candidates.append(
            TierState(tier=Tier.COMPUTE, node_id="npu-1", rtt_ms=15.0, multi_tenant=True,
                      capabilities=models.Capabilities(max_complexity=models.Complexity.LLM_REASON))
        )

    tiers = TierSet(candidates=candidates)

    print("declared:")
    print(f"  complexity={descriptor.complexity.wire} urgency={descriptor.urgency.wire} "
          f"privacy={descriptor.privacy.value} deadline_ms={descriptor.deadline_ms}")
    print("measured:")
    for c in tiers.candidates:
        marker = "*" if c.is_local else " "
        print(f" {marker} {c.tier.wire:<8} {c.node_id:<12} rtt={c.rtt_ms}ms "
              f"battery={c.power.battery_pct} mains={c.power.on_mains} "
              f"multi_tenant={c.multi_tenant}")

    try:
        res = resolve(task, tiers, DefaultPlacement())
    except PlacementDeferred as exc:
        print(f"\nresult: DEFERRED — {exc}")
        return 0
    except PlacementError as exc:
        print(f"\nresult: FAILED — {exc}")
        return 1

    print(f"\nresult: {res.explain()}")
    if res.considered:
        print(f"considered: {', '.join(res.considered)}")
    return 0


def cmd_conformance(args) -> int:
    cases = pathlib.Path(args.cases)
    if not cases.is_dir():
        print(f"no cases directory at {cases}", file=sys.stderr)
        return 1

    passed = failed = 0
    for case_file in sorted(cases.rglob("case.json")):
        case = json.loads(case_file.read_text(encoding="utf-8"))
        name = case_file.parent.relative_to(cases)

        if args.profile == "minimal" and case_file.parent.parent.name == "grant":
            # The minimal profile has no federation: such a device talks only to the hub that
            # commissioned it, and moving it is a re-commissioning operation.
            print(f"skip {name} (not required by profile 'minimal')")
            continue

        try:
            _run_case(case_file.parent, case)
            print(f"ok   {name}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {name}: {exc}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


def _run_case(case_dir: pathlib.Path, case: dict) -> None:
    from .core.codec import decode_cbor, decode_json

    # Grant cases carry `expect` as an object ({valid, reason}) rather than a string, so they must
    # be dispatched on the case shape before anything treats `expect` as a scalar.
    if "context" in case:
        _run_grant_case(case_dir, case)
        return

    expect = case["expect"]
    model = _MODELS[case["model"]]
    raw = json.loads((case_dir / "input.json").read_text(encoding="utf-8"))

    if expect == "reject":
        try:
            model.model_validate(raw)
        except Exception:
            return
        raise AssertionError(f"expected rejection ({case.get('reason')}) but it validated")

    doc = model.model_validate(raw)

    if expect == "roundtrip":
        again = decode_json(encode_json(doc), model)
        if again.model_dump(exclude_none=True) != doc.model_dump(exclude_none=True):
            raise AssertionError("JSON round trip changed the document")
        for field in case.get("preserve_unknown", []):
            if getattr(again, field, None) is None:
                raise AssertionError(f"unknown field {field!r} was dropped")

    elif expect == "json_cbor_equivalent":
        a = decode_json(encode_json(doc), model).model_dump(exclude_none=True)
        b = decode_cbor(encode_cbor(doc, int_keys=case.get("int_keys", True)), model)
        if b.model_dump(exclude_none=True) != a:
            raise AssertionError("CBOR and JSON decoded to different documents")

    elif expect == "size_bound":
        size = len(encode_cbor(doc, int_keys=case.get("int_keys", True)))
        if size > case["max_bytes"]:
            raise AssertionError(f"{size}B exceeds the {case['max_bytes']}B ceiling")

    else:
        raise AssertionError(f"unknown expectation {expect!r}")


def _run_grant_case(case_dir: pathlib.Path, case: dict) -> None:
    from .core.enums import AudienceKind, Scope
    from .security import capability

    ctx = case["context"]
    grant = models.CapabilityGrant.model_validate(
        json.loads((case_dir / "grant.json").read_text(encoding="utf-8"))
    )
    result = capability.verify(
        grant,
        audience_id=ctx["audience_id"],
        audience_kind=AudienceKind(ctx["audience_kind"]),
        tenant_id=ctx["tenant_id"],
        required_scope=Scope(ctx["required_scope"]),
        trusted_issuers={i: b"" for i in ctx["trusted_issuers"]},
        revoked=set(ctx.get("revoked", [])),
        now=datetime.fromisoformat(ctx["now"]),
        verify_signature=False,
    )
    exp = case["expect"]
    if result.valid != exp["valid"] or result.reason != exp["reason"]:
        raise AssertionError(
            f"expected valid={exp['valid']} reason={exp['reason']!r}, "
            f"got valid={result.valid} reason={result.reason!r}"
        )


def cmd_doctor(args) -> int:
    import platform

    from .placement.probe import local_capabilities, local_load, local_power

    print(f"python   : {platform.python_version()} ({platform.machine()})")
    print(f"platform : {platform.system()} {platform.release()}")
    print(f"power    : {local_power().model_dump(exclude_none=True)}")
    print(f"load     : {local_load().model_dump(exclude_none=True)}")
    print(f"caps     : {local_capabilities().model_dump(exclude_none=True)}")

    for extra, module in [("cbor", "cbor2"), ("hub", "flask"), ("compute", "grpc"),
                          ("archive", "boto3")]:
        try:
            __import__(module)
            print(f"extra    : {extra:<8} installed")
        except ImportError:
            print(f"extra    : {extra:<8} not installed")

    now = datetime.now(timezone.utc).isoformat()
    print(f"time     : {now}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qonclave", description="Qonclave developer tooling")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("spec-validate", help="check the JSON schemas parse and are well-formed")
    p.add_argument("--spec", help="path to spec/v1 (default: locate relative to this package)")
    p.set_defaults(func=cmd_spec_validate)

    p = sub.add_parser("conformance", help="run the language-neutral conformance fixtures")
    p.add_argument("--cases", required=True, help="path to conformance/cases")
    p.add_argument("--profile", default="full", choices=["full", "constrained", "minimal"])
    p.set_defaults(func=cmd_conformance)

    p = sub.add_parser("placement-explain", help="dry-run a task against simulated tier state")
    p.add_argument("--task", required=True, help="JSON file with a task descriptor")
    p.add_argument("--node-id", default="edge-local")
    p.add_argument("--battery", type=float, help="override measured battery percentage")
    p.add_argument("--no-hub", action="store_true", help="simulate an unreachable hub")
    p.add_argument("--no-compute-peer", action="store_true", help="simulate no compute node")
    p.set_defaults(func=cmd_placement_explain)

    p = sub.add_parser("doctor", help="report what this node measures about itself")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
