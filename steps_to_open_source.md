# Steps to Open Source Qonclave

This document outlines the systematic steps to prepare the Qonclave project for a public open-source release.

## 0. Repository Restructure (precedes the release)
*Qonclave's product is the framework; the current hub/edge code becomes reference material.*

Scoped 2026-08-06 against the actual tree (`git ls-files`, CI configs, and a grep for path-hacking
imports), after the `framework/sdk/` migration gave `hub/` a real SDK to depend on instead of only
itself. One finding changes the plan's risk profile for the better: **the API-honesty pass is
already mostly done.** Neither `hub/` nor `edge/` has a single `sys.path`/relative-filesystem reach
into `framework/sdk/python` — every SDK usage already goes through `import qonclave...` against the
installed package. The remaining gap is narrower than "audit for internal dependencies" implied:
`hub/framework/server.py` itself is not yet `qonclave.hub.app` (still a placeholder, per
`framework/docs/CONVENTIONS.md`'s "server.py's backwards dependency is fixed; the rest of the move
is descoped" section) — that's a real, but separately-tracked, later change, not a blocker for this
move.

**Phase A — License & hygiene prerequisites (independent, do first, no file moves)** ✅ `064f01d`
- [x] Add root `/LICENSE` (Apache-2.0) — copy of `framework/LICENSE`, which already exists at 772
  bytes and is correct; root currently has none.
- [x] Add root `/NOTICE` per §2.
- [x] Add `.claude/`, `.gemini/`, `.obsidian/`, `QUAD/` to root `.gitignore` (confirmed absent
  today) — cheap, unblocks nothing else, but should land before any PR that touches the tree so
  these never get swept into a commit by accident.

**Phase B — Move the trees** ✅ `2cda02e`
- [x] `git mv hub examples/hub`, `git mv edge examples/edge` (preserves history; confirmed no
  tracked file outside these two trees needs to change for the move itself — no path-hacking
  imports found anywhere in either).
- [x] `edge/arduino_uno_q_00/qonclave-person-emotions/` is untracked (0 files in git) — not part of
  this move; left untouched.
- [x] Judgment call: `shared/` and `demo/` (root-level, referenced only in `README.md`'s layout
  block, currently just `.gitkeep` placeholders each) are demo-support material, not framework —
  folded into `examples/shared/` and `examples/demo/` in the same move for consistency.
- [x] Added `/examples/LICENSE` (0BSD) per §2, in the same commit as the move.

**Phase C — Fix the path breaks this move causes (confirmed, not hypothetical)** ✅ `2cda02e`
- [x] `examples/hub/setup_hub.ps1`, `setup_mqtt.ps1`, and `setup_ngrok.ps1` **all three** — fixed
  to compute repo root as two levels up: `Split-Path (Split-Path $PSScriptRoot -Parent) -Parent`
  (or `Split-Path (Split-Path $HubDir -Parent) -Parent` for ngrok). Preserves exact behavior;
  `$SdkDir = Join-Path $RepoDir 'framework\sdk\python'` now resolves to `<repo>/framework/sdk/python`.
- [x] Verified everything else checked (`hub/tests/*.py`, `edge/.../test_*.py`, `hub/server.py`'s
  own `sys.path.insert`) is self-relative to `__file__` and unaffected by the move.

**Phase D — Retarget CI** ✅ `2cda02e`
- [x] `hub.yml`: `paths:` trigger `hub/**` → `examples/hub/**` (kept `framework/sdk/python/**`
  unchanged — correct regardless of where `hub/` lives); updated install/test paths
  (`hub/requirements.txt` → `examples/hub/requirements.txt`, `pytest hub/tests` → `pytest examples/hub/tests`).
- [x] `edge.yml`: `paths:` trigger and `working-directory` → `examples/edge/...` equivalent.
- [x] `framework.yml` verified: no changes needed (already self-contained to `framework/**`).

**Phase E — Docs rewrite** ✅ `2cda02e`
- [x] Root `README.md`: rewrote `## Layout` block to show framework at root, examples/ as the app
  container; added framework/spec/conformance/sdks hierarchy; updated 9 instances of
  `.\hub\setup_hub.ps1` → `.\examples\hub\setup_hub.ps1`; updated all `hub/` and `hub/requirements.txt`
  references to `examples/hub/...`.
- [x] `examples/hub/README.md`: added reference-material banner explaining it's now consuming
  `qonclave` via `pip install`.
- [x] `framework/README.md`'s "Relationship to `hub/` and `edge/`" section verified — was already
  updated this session to describe the current shim state; no further change needed.

**Phase F — Verification** ✅
- [x] Structure clean: old root paths (hub/, edge/, shared/, demo/) gone; new examples/* all exist.
- [x] `git log --follow` through the move: history preserved (sample trace on mqtt_bus.py shows
  full lineage through 2cda02e back to earlier commits).
- [x] PowerShell fixes in place and verified: all three scripts updated.
- [x] CI paths correct in both hub.yml and edge.yml, framework.yml untouched.

Sequencing note: Phase A has no dependency on B–F and can land as its own small PR first. B–D must
land together (a mid-move CI or script break is exactly the kind of thing that should never be its
own commit). E can mostly ride in the same PR as B–D per the existing "never contradict" rule above,
except the `framework/docs/*.md` grep sweep, which only makes sense to run once the move is real.

## 1. History Rewrite (one-time, batched — decided)
*Personal photos and unlicensed media must not ship in the public history. A rewrite is disruptive, so everything unwanted goes in ONE `git filter-repo` pass.*

Paths to purge from history:
- [ ] **Face photos** (decided: scrub): `hub/framework/face_id/known_faces/jogendra*.jpg` — enrolled biometric images of a real person.
- [ ] **Sample media of unknown provenance** (decided: scrub): `hub/apps/security/samples/*.jpg|png` (intruder_cctv, senior_fall, room_with_person, empty_room, geniex_demo) and `edge/.../media/walking_front_view.mp4`.
- [ ] **Hackathon artifacts** (decided: remove): `docs/` proposal, slides, `.docx`/`.pdf`, `HACKATHON.md`. Triage `docs/` per file first — internal notes (`scratch.txt`, `hub_code_assessment.md`, task lists) go; genuinely useful design docs may stay in the tree.
- [ ] **Committed binary wheels**: `hub/framework/face_id/wheels/` carries 25 MB of OpenCV `.whl` files (repo pack is already 62 MB). Download them in `setup_hub.ps1` instead; purging them from history also shrinks every future public clone.

Replacements (so nothing breaks):
- [ ] **Regenerate synthetic samples** via `hub/apps/security/samples/make_samples.py` (Pillow-based; this is what it exists for). Tests are unaffected — they use `tmp_path` fixtures, not the committed images.
- [ ] **Consented or synthetic enrollment face** for the face-ID quickstart, replacing the personal photos.
- [ ] **Genericize personal names in code**: `hub/apps/security/known_person_priorities.py` hardcodes `{"jogendra": {"priority": 1}}` as the example — use a placeholder name.

Sequencing and fallout:
- [ ] Land all tree-level cleanup first, run the scrub as the **final step before going public**, then transfer to the org. The rewrite changes every commit hash: all teammates must re-clone, and any open PRs against the old history die — coordinate a freeze window.
- [ ] Use `git filter-repo` (not BFG); verify afterward with `git log --all --diff-filter=A` and a `gitleaks` history scan.

## 2. Licensing & Legal
*Decided: Apache 2.0 for the framework, 0BSD for examples.*

Rationale (from Qonclave's nature as a developer framework, independent of dependency constraints):
- A framework's existential risk is **obscurity, not closed forks** — a fork loses the ecosystem, which is where a framework's value lives. Permissive licensing makes adoption a non-decision.
- Apps sit **directly on the API** (subclassing `Policy`, static-linking the C SDK into firmware), so copyleft would infect adopters' products: GPL makes every app GPL; LGPL's relink requirement is unworkable for statically linked firmware; AGPL deters hub/server deployments. No successful developer framework of the last 15 years is copyleft (see Appendix).
- **Apache 2.0 over MIT** because of what "developers join in" requires: §5 makes every contribution automatically licensed inbound=outbound (safe to merge drive-by PRs with just a DCO, no CLA); the patent grant + retaliation clause is the treaty that lets hardware/AI companies co-develop safely; §6 reserves the **Qonclave trademark** — the real control point in a permissive ecosystem (Home Assistant demonstrates this working in practice).
- Dependency stack was verified separately: all permissive (Flask/BSD, pydantic/MIT, MediaPipe/Apache, vosk/Apache, mbedTLS dual Apache-2.0/GPLv2+ and only an optional backend, etc.). Nothing blocks Apache 2.0, and `framework/sdk/python/pyproject.toml` + `framework/LICENSE` already declare it.
- **No LLVM-style exception**: the LLVM exception exists for runtime libraries compiled into every user binary and GPLv2 compatibility — neither applies here, and nonstandard license text scares corporate adopters more than plain Apache 2.0.
- Trade-off accepted: permissive forecloses copyleft+CLA dual-licensing revenue (MongoDB/Qt model). Future business paths remain support, hosted services, certified hardware, enterprise distribution (Kubernetes playbook).

Steps:
- [ ] **Add `/LICENSE`**: Standard Apache 2.0 text at the repository root (governs everything except `examples/`).
- [ ] **Add `/examples/LICENSE`**: 0BSD (zero-attribution), so developers can copy reference code into their own projects with no attribution obligation (the AWS-samples MIT-0 pattern). State "copy freely, no attribution required" in the examples README.
- [ ] **Add `/NOTICE`**: One-liner — "Qonclave — Copyright 2025-2026 the Qonclave contributors."
- [ ] **Contributor buy-in**: Written agreement from every existing committer (jogendar, teammates) to Apache 2.0 **before** going public. Relicensing is cheap now, near-impossible after external contributions land (see ZeroMQ in the Appendix).
- [ ] **Third-party models note**: README section stating model weights (Qwen-VL variants, CavaFace, Vosk, Piper voices) are downloaded separately under their own licenses — the code license does not cover them.

## 3. Name & Canonical Home
- [ ] **Register the name early** — verified available as of 2026-08-06: PyPI `qonclave` (free) and github.com/qonclave (free). Grab the PyPI name, create the org, register a domain (qonclave.org), and do a quick trademark-conflict search. Free today, painful to recover if squatted after launch.
- [ ] **Create a GitHub organization** (decided): A neutral, team-owned home.
- [ ] **Transfer, don't re-push**: Transfer the canonical repo (`jogendar/Qonclave`, which the README advertises) into the org — GitHub preserves stars/issues and sets up automatic redirects. Requires coordination with jogendar. Do this **after** the §1 history scrub.

## 4. Community Standards & Guidelines
*These documents help manage community expectations and guide contributions.*
- [ ] **`CONTRIBUTING.md`** (root, consolidated per §0): Dev environment setup, PR instructions, code style (referencing `AGENTS.md` and standard Python/C practices), the spec-is-normative rule, and a warning that AGPL dependencies (e.g. Ultralytics/YOLO — currently mentioned only in research docs) must never be wired into the framework.
- [ ] **`CODE_OF_CONDUCT.md`**: Standard Contributor Covenant. (Decide contact email: project address vs bnr.robotics@gmail.com.)
- [ ] **`SECURITY.md`**: Instructions for reporting vulnerabilities privately (same email decision).
- [ ] **Enforce DCO (Developer Certificate of Origin)**: Instead of a heavy CLA, require `git commit -s` via the DCO GitHub App. Combined with Apache 2.0 §5 this is the Linux Foundation standard combo — contributions arrive safe to merge with no CLA negotiation.
- [ ] **`GOVERNANCE.md`**: BDFL or core-team model for now; answers the "bus factor" question for adopting companies.

## 5. GitHub Issue & PR Templates
*Templates ensure that incoming issues and PRs have consistent and useful information.*
- [ ] **Add Bug Report Template**: `.github/ISSUE_TEMPLATE/bug_report.yml` for reproducible bug reports.
- [ ] **Add Feature Request Template**: `.github/ISSUE_TEMPLATE/feature_request.yml` for feature proposals.
- [ ] **Add PR Template**: `.github/pull_request_template.md` with a checklist (tests passing, docs updated, DCO sign-off).

## 6. Documentation & Distribution (adoption-critical)
- [ ] **Adopter docs**: Quickstart that works **without the hardware** (pairs with mock mode in §9), architecture overview, API reference generated from the SDK (mkdocs/Sphinx). For a framework this outweighs everything else on adoption.
- [ ] **Distribution story**: Publish the Python SDK to PyPI with a release workflow; decide how the C SDK is consumed (ESP-IDF component registry, PlatformIO library, Zephyr module, or vendoring).
- [ ] **Release process**: Tag `v0.1.0-alpha` at release; state in the README that APIs are subject to change. Strict SemVer thereafter — users subclass `framework.policy.Policy`, so breaking changes break downstream apps. GitHub Releases + keep the existing `CHANGELOG.md` going.
- [ ] **Contributor on-ramp**: `ROADMAP.md` or pinned issues, `good-first-issue` labels, a stated home for questions (GitHub Discussions vs Discord).

## 7. Project Cleanup & Refinement
- [ ] **Review `README.md`**: Orient entirely toward general open-source users; add badges (License, CI status); add the third-party models section.
- [ ] **Privacy-claims audit**: README promises "zero-trust air-gapping" and "raw data never leaves the subnet" — public scrutiny of a privacy-first project starts there. Add a short threat-model doc (anchored on `framework/spec/`) distinguishing what the framework *enforces* from what is *aspirational*.
- [ ] **Untracked local clutter**: Add `.claude/`, `.gemini/`, `.obsidian/`, and `QUAD/` to `.gitignore` so they can never be accidentally committed publicly.

## 8. Security & Verification
- [ ] **Verify Secrets**: Quick greps found only `.env.example` files and no hardcoded credentials; run a proper history scan (`gitleaks`) as part of the §1 scrub verification.
- [ ] **Verify GitHub Settings**: Branch protection enabled; GitHub Discussions turned on (optional but recommended).
- [ ] **Enable Automated Secret Scanning**: GitHub secret scanning + push protection, so future contributors can't leak Qualcomm AI Hub tokens or Twilio keys in PRs.

## 9. Strategic Framework Considerations
*Because Qonclave is an edge-AI framework interfacing with specialized hardware, extra care is needed for community growth.*
- [ ] **Hardware Accessibility (Mocks)**: Build a "CPU-only mock mode" / hardware simulators so developers without a Snapdragon X or Arduino UNO Q can test contributions (framework logic testable with the VLM stubbed out).

## Appendix: Industry License Research

**Question**: What licenses do other major open-source frameworks and middleware use, specifically in the AI, edge, and robotics space?

**Summary of Results**:
- **Apache License 2.0**: The dominant standard for modern infrastructure and frameworks. Used by **ROS 2** (Robot Operating System), **Kubernetes**, **TensorFlow**, **gRPC**, and **Apache Kafka**. This license is preferred because it explicitly protects contributors and users from patent litigation, which is critical in hardware, robotics, and complex distributed systems.
- **BSD / MIT**: Used by some pure software libraries or research-driven projects that prioritize absolute lack of friction over explicit patent protections. Examples include **PyTorch** (BSD-3-Clause) and **ONNX Runtime** (MIT).
- **Copyleft (GPL/AGPL)**: Noticeably absent from successful developer frameworks. If a framework uses a strong copyleft license like GPL, any proprietary commercial product built on top of it would be forced to open-source its entire codebase, effectively killing commercial adoption.

Conclusion: The decision to use Apache 2.0 for Qonclave aligns perfectly with the proven trajectory of the most successful developer frameworks in the world.

**Researcher**: Antigravity

---

**Question**: Weighted by similarity to Qonclave (frameworks/middleware over applications), what do license choices and *license migrations* in comparable projects tell us?

**Summary of Results**:
- **Tier 1 — direct analogs (edge/robotics middleware, on-device AI)**: ROS 2, EdgeX Foundry (LF Edge), KubeEdge & Akri (CNCF), Fast DDS, micro-ROS, MediaPipe, LiteRT/TFLite, OpenVINO — all **Apache 2.0**; Eclipse zenoh is EPL-2.0/Apache-2.0 dual; ONNX Runtime (MIT), ExecuTorch (BSD-3). Every project in Qonclave's own category is permissive.
- **Tier 2 — messaging middleware**: NATS, gRPC, EMQX (Apache 2.0); Mosquitto/Paho (EPL/EDL dual — the EDL half is BSD-3, added precisely because EPL alone blocked embedded adoption); nanomsg/NNG (MIT).
- **Tier 3 — embedded platforms (the C SDK's targets)**: Zephyr, ESP-IDF, Mbed OS, NuttX — all Apache 2.0. RIOT OS is the LGPL holdout, with commercial uptake a fraction of Zephyr's.
- **License migrations all went one direction, copyleft → permissive**: **ZeroMQ** relicensed LGPL(+exception) → MPL-2.0 (completed v4.3.5) because corporate lawyers refused the nonstandard copyleft license — the effort took years, required relicensing grants from every author, and clean-room rewrites of code from unreachable authors. **FreeRTOS** went GPL+exception → MIT (2017, Amazon) explicitly to remove adoption friction. No framework has ever migrated permissive → copyleft to fix an adoption problem.
- **Copyleft survives only in end-user products, not frameworks**: Tasmota (GPL-3.0), ESPHome's C++ runtime (GPLv3). Instructively, **ESPHome licenses its developer-facing framework layer (Python) MIT** while keeping GPL only on the end-user runtime — another team independently concluding the layer developers build on must be permissive.
- **Trademark as the governance lever**: Home Assistant (Apache 2.0) actively enforces its trademark as its control point — live validation of the Apache §6 strategy.

Conclusion: Weighted by similarity, the evidence is essentially unanimous for Apache 2.0 on the framework, and ZeroMQ is the cautionary tale to cite against copyleft proposals: that community spent years buying back the adoption a nonstandard copyleft license cost them. The framework (Apache 2.0) / examples (0BSD) split mirrors ESPHome's own layering conclusion.

Sources: [ZeroMQ relicense PR #4555](https://github.com/zeromq/libzmq/pull/4555) · [ZeroMQ license page](https://zeromq.org/license/) · [ESPHome LICENSE](https://github.com/esphome/esphome/blob/dev/LICENSE) · [LWN on ESPHome](https://lwn.net/Articles/823131/) · [Frigate LICENSE](https://github.com/blakeblackshear/frigate/blob/dev/LICENSE)

**Researcher**: Claude Fable 5
