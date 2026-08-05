"""
policy.py — the app contract, now supplied by the qonclave SDK.

This module used to define `Policy`, `Verdict` and `Notification`. They now live
in `qonclave.hub.policy` and are re-exported here so that every existing import

    from framework.policy import Policy, Verdict, Notification

keeps working while `hub/` converges on `framework/`. When hub/framework/ is
finally absorbed, this file goes away and apps import qonclave.hub directly.

What changed for an app subclassing Policy:

    evaluate(image_path, event: dict)   ->  evaluate(event: EdgeEvent, image_path=None)
    command_for(...) -> dict | None     ->  -> Command | None
    on_sms_reply / reply_for_sms        ->  on_reply / reply_for
    last_sms_analysis()                 ->  dashboard_state()

`Verdict` and `Notification` are positionally compatible with the old ones — the
new definitions only add defaults and a `urgency` field — so existing
constructions need no change.

`image_path` is now optional because a payload-free event is a real event: a
threshold crossing from a duty-cycled sensor has nothing to look at, and a
Policy that assumes an image crashes on the smallest devices in the fleet.

`dashboard_state()` replaces `last_sms_analysis()`. The old name described one
app's use of one transport; the new one is a generic hook the framework serves
without interpreting, which is why it did not need to become
`last_whatsapp_analysis()` the moment a second app appeared.
"""

from __future__ import annotations

from qonclave.hub.policy import Notification, Policy, Verdict

__all__ = ["Policy", "Verdict", "Notification"]
