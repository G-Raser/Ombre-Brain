import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import _runtime as rt
from utils import strip_wikilinks


_CREATED_ALIASES = {"created", "added"}
_UPDATED_ALIASES = {"updated", "active"}


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _list_from_value(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.replace("，", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()]


def _parse_filter_list(value: Optional[str]) -> list[str]:
    return [part.strip() for part in str(value or "").replace("，", ",").split(",") if part.strip()]


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if len(text) == 10:
            text = f"{text}T00:00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None

    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _time_value(meta: dict[str, Any], mode: str) -> tuple[str, str, datetime | None]:
    if mode in _CREATED_ALIASES:
        candidates = (
            ("created", meta.get("created")),
            ("created_at", meta.get("created_at")),
            ("last_active", meta.get("last_active")),
            ("updated_at", meta.get("updated_at")),
            ("modified", meta.get("modified")),
            ("updated", meta.get("updated")),
        )
    else:
        candidates = (
            ("last_active", meta.get("last_active")),
            ("updated_at", meta.get("updated_at")),
            ("modified", meta.get("modified")),
            ("updated", meta.get("updated")),
            ("created", meta.get("created")),
            ("created_at", meta.get("created_at")),
        )

    for field, raw in candidates:
        parsed = _parse_dt(raw)
        if parsed is not None:
            return field, str(raw or ""), parsed
    return "", "", None


def _created(meta: dict[str, Any]) -> str:
    return str(meta.get("created") or meta.get("created_at") or "")


def _updated(meta: dict[str, Any]) -> str:
    return str(
        meta.get("last_active")
        or meta.get("updated_at")
        or meta.get("modified")
        or meta.get("updated")
        or meta.get("created")
        or meta.get("created_at")
        or ""
    )


def _matches_query(bucket: dict[str, Any], meta: dict[str, Any], query: str) -> bool:
    if not query:
        return True
    tags = _list_from_value(meta.get("tags"))
    domains = _list_from_value(meta.get("domain"))
    haystack = "\n".join(
        [
            str(bucket.get("content") or ""),
            str(meta.get("name") or ""),
            str(meta.get("title") or ""),
            " ".join(tags),
            " ".join(domains),
            str(meta.get("why_remembered") or ""),
        ]
    ).lower()
    return query in haystack


def _preview(content: str) -> str:
    text = " ".join(strip_wikilinks(content or "").split())
    if len(text) <= 240:
        return text
    return text[:240].rstrip() + "..."


async def recent_buckets(
    window_days: Optional[int] = 3,
    mode: Optional[str] = "updated",
    query: Optional[str] = "",
    tags: Optional[str] = "",
    domain: Optional[str] = "",
    bucket_type: Optional[str] = "",
    max_results: Optional[int] = 20,
    include_content: Optional[bool] = False,
    content_max_chars: Optional[int] = 800,
) -> str:
    window_days = _clamp_int(window_days, 3, 1, 30)
    max_results = _clamp_int(max_results, 20, 1, 50)
    content_max_chars = _clamp_int(content_max_chars, 800, 1, 8000)

    mode_norm = str(mode or "updated").strip().lower()
    if mode_norm in _CREATED_ALIASES:
        mode_norm = "created"
    elif mode_norm in _UPDATED_ALIASES:
        mode_norm = "updated"
    else:
        mode_norm = "updated"

    query_text = str(query or "").strip().lower()
    required_tags = [tag.lower() for tag in _parse_filter_list(tags)]
    domain_filters = [item.lower() for item in _parse_filter_list(domain)]
    type_filter = str(bucket_type or "").strip().lower()
    include_full = bool(include_content)

    now = datetime.now()
    cutoff = now - timedelta(days=window_days)

    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"recent_buckets failed: {e}"}, ensure_ascii=False)

    results: list[dict[str, Any]] = []
    for bucket in all_buckets:
        meta = bucket.get("metadata", {}) or {}
        if meta.get("deleted_at"):
            continue

        b_type = str(meta.get("type") or "dynamic")
        if type_filter and b_type.lower() != type_filter:
            continue

        tag_list = _list_from_value(meta.get("tags"))
        tag_set = {tag.lower() for tag in tag_list}
        if required_tags and not all(tag in tag_set for tag in required_tags):
            continue

        domain_list = _list_from_value(meta.get("domain"))
        domain_set = {item.lower() for item in domain_list}
        if domain_filters and not any(item in domain_set for item in domain_filters):
            continue

        if not _matches_query(bucket, meta, query_text):
            continue

        used_field, time_basis, parsed_time = _time_value(meta, mode_norm)
        if parsed_time is None or parsed_time < cutoff:
            continue

        content = str(bucket.get("content") or "")
        clean_content = strip_wikilinks(content)
        item: dict[str, Any] = {
            "bucket_id": bucket.get("id") or meta.get("id") or "",
            "name": meta.get("name") or meta.get("title") or bucket.get("id") or "",
            "title": meta.get("title") or meta.get("name") or "",
            "type": b_type,
            "status": meta.get("status") or "",
            "created": _created(meta),
            "last_active": meta.get("last_active") or "",
            "updated_at": meta.get("updated_at") or meta.get("modified") or meta.get("updated") or "",
            "used_time_field": used_field,
            "time_basis": time_basis,
            "_time_sort": parsed_time.timestamp(),
            "domain": domain_list,
            "tags": tag_list,
            "importance": meta.get("importance", 5),
            "weight": meta.get("weight") if b_type == "plan" else None,
            "content_preview": _preview(content),
            "content_length": len(clean_content),
        }
        if include_full:
            item["content"] = clean_content[:content_max_chars]
        results.append(item)

    results.sort(key=lambda item: item.get("_time_sort") or 0, reverse=True)
    for item in results:
        item.pop("_time_sort", None)
    results = results[:max_results]
    return json.dumps(
        {
            "ok": True,
            "window_days": window_days,
            "mode": mode_norm,
            "query": str(query or "").strip(),
            "tags": _parse_filter_list(tags),
            "domain": _parse_filter_list(domain),
            "bucket_type": str(bucket_type or "").strip(),
            "count": len(results),
            "max_results": max_results,
            "include_content": include_full,
            "content_max_chars": content_max_chars if include_full else 0,
            "results": results,
        },
        ensure_ascii=False,
        indent=2,
    )
