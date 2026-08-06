"""
envelope.py -- schema_version stamping and validation.

Applies the version rule from spec/v1/README.md: reject a major version we do not implement,
accept any minor within a major we do. 1.0 and 1.7 are mutually intelligible; 2.0 is not assumed
to be.

Spec: spec/v1/json-schema/common.schema.json#/$defs/schemaVersion
"""

from __future__ import annotations
