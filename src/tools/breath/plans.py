"""
Lightweight active plan side blocks for breath-like surfaces.
"""

from .. import _runtime as rt
from utils import strip_wikilinks


def _bucket_tags(meta: dict) -> list[str]:
    raw = meta.get("tags") or []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


def _has_required_tags(meta: dict, tag_filter: list[str]) -> bool:
    if not tag_filter:
        return True
    tags = set(_bucket_tags(meta))
    return all(t in tags for t in tag_filter)


def _current_plan_status(meta: dict) -> str:
    value = meta.get("status")
    if value is None or str(value).strip() == "":
        return "active"
    return str(value).strip().lower()


def is_active_plan_bucket(bucket: dict) -> bool:
    meta = bucket.get("metadata", {}) or {}
    if meta.get("type") != "plan":
        return False
    return _current_plan_status(meta) == "active"


def _matches_query(bucket: dict, query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    meta = bucket.get("metadata", {}) or {}
    parts = [
        str(bucket.get("content") or ""),
        str(meta.get("name") or ""),
        str(meta.get("title") or ""),
        " ".join(_bucket_tags(meta)),
        str(meta.get("why_remembered") or ""),
    ]
    return q in "\n".join(parts).lower()


def _time_key(meta: dict) -> str:
    return str(
        meta.get("last_active")
        or meta.get("updated")
        or meta.get("updated_at")
        or meta.get("created")
        or meta.get("created_at")
        or ""
    )


def _preview(content: str, limit: int = 160) -> str:
    text = " ".join(strip_wikilinks(content or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _active_plan_candidates(
    buckets: list[dict],
    *,
    query: str = "",
    tag_filter: list[str] | None = None,
) -> list[dict]:
    tags = tag_filter or []
    plans = []
    for bucket in buckets:
        meta = bucket.get("metadata", {}) or {}
        if not is_active_plan_bucket(bucket):
            continue
        if not _has_required_tags(meta, tags):
            continue
        if query and not _matches_query(bucket, query):
            continue
        plans.append(bucket)

    def _sort_key(bucket: dict) -> tuple[float, str]:
        meta = bucket.get("metadata", {}) or {}
        try:
            weight = float(meta.get("weight") if meta.get("weight") is not None else 0.5)
        except (TypeError, ValueError):
            weight = 0.5
        return (weight, _time_key(meta))

    plans.sort(key=_sort_key, reverse=True)
    return plans


def format_active_plans_from_buckets(
    buckets: list[dict],
    *,
    title: str,
    query: str = "",
    tag_filter: list[str] | None = None,
    max_items: int = 3,
    preview_chars: int = 160,
    max_block_chars: int = 800,
) -> str:
    items = _active_plan_candidates(buckets, query=query, tag_filter=tag_filter)
    if not items:
        return ""

    lines = [title]
    for bucket in items[:max(1, max_items)]:
        meta = bucket.get("metadata", {}) or {}
        created = str(meta.get("created") or meta.get("created_at") or "")[:10]
        try:
            weight = float(meta.get("weight") if meta.get("weight") is not None else 0.5)
        except (TypeError, ValueError):
            weight = 0.5
        line = (
            f"[{bucket.get('id') or meta.get('id') or ''}] "
            f"{created} · weight={weight:g} · "
            f"{_preview(str(bucket.get('content') or ''), preview_chars)}"
        )
        next_text = "\n".join(lines + [line])
        if len(next_text) > max_block_chars:
            break
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""


async def format_active_plans_block(
    *,
    title: str,
    query: str = "",
    tag_filter: list[str] | None = None,
    max_items: int = 3,
    preview_chars: int = 160,
    max_block_chars: int = 800,
) -> str:
    try:
        buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as e:
        rt.logger.warning(f"active plans block failed to list buckets: {e}")
        return ""
    return format_active_plans_from_buckets(
        buckets,
        title=title,
        query=query,
        tag_filter=tag_filter,
        max_items=max_items,
        preview_chars=preview_chars,
        max_block_chars=max_block_chars,
    )
