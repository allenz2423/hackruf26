"""Deterministic helpers for optimistic study-guide section edits."""

from __future__ import annotations

import hashlib
import json


def guide_section_fingerprint(section: object) -> str:
    """Fingerprint the exact saved section, including its private check key."""
    if hasattr(section, "model_dump"):
        value = section.model_dump(mode="json", exclude_none=True)
    elif isinstance(section, dict):
        value = section
    else:
        raise TypeError("A saved guide section is required.")
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
