"""
sms.py -- SMS notifications.

Origin: hub/framework/sms_bus.py

Deliberately left as a placeholder rather than a generic contract: SMS (which vendor, which
credentials, which activity-tracking shape) turned out to be entirely app-specific in practice --
see hub/apps/security/egress/twilio_sms.py, which implements it without importing anything from
here. See CONVENTIONS.md's note on the sms_bus.py migration.
"""

from __future__ import annotations
