"""
Status normalization for formal plan buckets.
"""


def normalize_plan_status_for_formal(value, default: str = "active") -> str:
    allowed = {"active", "resolved", "abandoned"}
    fallback = default if default in allowed else "active"
    raw = str(value or "").strip().lower()
    if raw in allowed:
        return raw
    return fallback

