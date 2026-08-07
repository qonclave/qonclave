# Security Policy

This is Qonclave's vulnerability disclosure policy — how to report a
security issue, and what to expect after you do. For the framework's
*design* — threat model, transport security, capability grants, tenancy —
see [`framework/docs/SECURITY.md`](framework/docs/SECURITY.md).

## Supported Versions

Qonclave has not yet made a tagged release (`v0.1.0-alpha` is planned — see
`steps_to_open_source.md` §6). Until then, only the `main` branch is
supported; report issues against the latest commit.

| Version | Supported |
|---|---|
| `main` (pre-release) | ✅ |

Once tagged releases exist, this table will track the currently-supported
major/minor versions per standard SemVer support windows.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**
A public issue gives attackers a head start before a fix ships.

Instead, report privately using one of:

1. **GitHub Security Advisories** (preferred): use the "Report a
   vulnerability" button under this repository's Security tab. This keeps
   the discussion private between you and the maintainers until a fix is
   ready.
2. **Email**:

<!-- TODO(2026-08-07): decide the contact address before this goes public --
     see steps_to_open_source.md §4. Same decision as CODE_OF_CONDUCT.md's
     contact address -- likely the same address for both. -->

   **security@qonclave.org** (placeholder — not yet live; use
   bnr.robotics@gmail.com until then)

Please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce (a minimal repro is ideal)
- The affected component (`framework/sdk/python`, `framework/sdk/c`,
  `examples/hub`, `examples/edge`, or the spec itself)
- Your assessment of severity, if you have one

## What to Expect

- **Acknowledgment** within 5 business days.
- We'll work with you to understand and confirm the issue, and agree on a
  disclosure timeline before anything is made public. We aim to ship a fix
  or mitigation within 90 days of confirmation, sooner for critical issues.
- Credit in the fix's release notes and `CONTRIBUTORS.md`, if you'd like it.

## Scope

In scope: `framework/sdk/python`, `framework/sdk/c`, `framework/spec/v1`,
and the reference apps under `examples/hub` and `examples/edge` as shipped
in this repository.

Out of scope: third-party model weights, dependencies' own vulnerabilities
(report those upstream), and anything in a fork or downstream deployment
not shipped by this project.

## Known Gaps (disclosed, not hidden)

The reference deployment under `examples/hub` does not yet implement the
authentication/authorization layer described in `framework/docs/SECURITY.md`
§3 and §5 (capability grants, mTLS, commissioning) — it is reference
material for a LAN-trusted demo, not a hardened production deployment.
Treating an `examples/hub` instance as internet-facing (e.g. tunneled via
ngrok) without adding your own authentication layer is a known gap; hardening
it to match the design in `framework/docs/SECURITY.md` is tracked work, not
an oversight nobody knows about.
