# Contributing to Qonclave

Thanks for considering a contribution. This document covers the whole repo;
`framework/CONTRIBUTING.md` has additional, SDK-specific detail (the spec
workflow, conformance fixtures, layering rules) that applies only inside
`framework/`.

By participating, you're expected to follow the [Code of
Conduct](CODE_OF_CONDUCT.md).

## Repo layout

- **`framework/`** is the product: the installable `qonclave` Python
  package, the C SDK, and the normative `spec/v1/` — hardware-agnostic,
  use-case-agnostic middleware.
- **`examples/`** is reference material: a security-camera hub app and an
  Arduino UNO Q edge app, both built *on* the framework, showing what a real
  deployment looks like. Not part of the installable package.

See the root [`README.md`](README.md) for the full architecture.

## The one rule that is not negotiable

**`framework/spec/v1/` is normative.** Where the SDK and the spec disagree,
the SDK is wrong and the fix goes in the SDK — never change a schema to make
code pass. Within v1, changes must be **additive** (new optional fields, new
values in open sets); removing a field or tightening a constraint needs v2,
because devices commissioned years ago are still in the field and cannot be
updated. See `framework/docs/CONVENTIONS.md` for the full rationale.

## Dev environment setup

**Framework SDK:**
```bash
pip install -e "framework/sdk/python[dev]"
python -m pytest framework/sdk/python/tests -v
python -m qonclave.cli spec-validate
python -m qonclave.cli conformance --cases framework/conformance/cases
```

**`examples/hub`** (Windows/PowerShell): `.\examples\hub\setup_hub.ps1` sets
up the `geniex-env` virtual environment and installs the SDK into it — use
this rather than a manual `pip install` for core dependencies. See
`examples/hub/README.md`.

**`examples/edge`**: each device folder is a standalone app with its own
setup — see the `README.md` inside the specific device directory (e.g.
`examples/edge/arduino_uno_q_00/qonclave-detect-objects-on-camera/README.md`).

## Code style

- Python: PEP 8, type-hinted where the surrounding code already is. Follow
  the file you're editing's existing conventions before introducing new ones.
- C (SDK): match `framework/sdk/c`'s existing style — no new dependencies
  without discussion, since this code targets constrained/embedded targets.
- No application-specific logic in `framework/` or `examples/hub/framework/`
  — those stay use-case-agnostic. New use cases live entirely under
  `examples/hub/apps/<app_name>/`, subclassing `framework.policy.Policy`
  (or `qonclave.hub.policy.Policy`) and implementing `evaluate()`. See
  `AGENTS.md` for the details this project holds itself to strictly.

## License compatibility — read before adding a dependency

Qonclave is Apache 2.0 (`examples/` is 0BSD). **Do not add a GPL, LGPL, or
AGPL-licensed dependency to `framework/`** — apps subclass the framework's
API directly and statically link the C SDK into firmware, so copyleft in the
framework would infect every adopter's product. This has already come up in
research discussions around Ultralytics/YOLO (AGPL) — it must never be wired
into `framework/`. Permissive-only (MIT/BSD/Apache/similar) is the bar for
anything `framework/` depends on; `examples/` has more latitude since it
never ships as a library. See `steps_to_open_source.md` §2 for the full
licensing rationale.

## What reviewers will ask (framework/ changes)

- **Does it respect the layering?** `framework/sdk/python/tests/test_layering.py`
  enforces it: role packages never import each other, which is what keeps an
  edge install free of a web framework and makes Compute/Archive roles
  genuinely optional.
- **What does it cost a device that wakes for 200ms once a day?** Round
  trips are the budget, not bytes. A field added to the check-in exchange is
  paid for by every sensor in every deployment, forever.
- **Does behavior that crosses implementations have a fixture?** If a C
  binding could plausibly get it wrong, it belongs in `framework/conformance/`,
  not only in a Python test.

## Commit messages

Imperative mood, capitalized subject, no trailing period, subject line under
72 characters, blank line before the body. Explain *what* and *why*, not
just what changed — the diff already shows what changed.

## Sign-off (DCO)

Every commit must be signed off (`git commit -s`), certifying you wrote it
or otherwise have the right to submit it under the project's license (the
[Developer Certificate of Origin](https://developercertificate.org/)). This
is how Qonclave takes contributions without a separate CLA.

## Before you open a PR

- Tests pass locally for whatever you touched (`framework/sdk/python`,
  `examples/hub/tests`, or the relevant `examples/edge/.../test_*.py`).
- `spec-validate`/`conformance` pass if you touched `framework/spec/` or
  any SDK codec.
- Commits are signed off (`-s`).
- New behavior has a test; changed behavior has an updated one.
