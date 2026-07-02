"""
Journal storage for handoff / event / free entries.

Journals are intentionally separate from normal memory buckets. They keep the
full Markdown body under buckets/journals/ and are searched by lightweight
metadata + keyword matching for the MVP.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import _runtime as rt


ENTRY_TYPES = {"handoff", "event", "free"}
STATUSES = {"active", "archived"}
DEFAULT_IMPORTANCE = 5
JOURNAL_ID_RE = re.compile(r"^journal-\d{8}-\d{6}-\d{3}$")


def _buckets_dir() -> Path:
    cfg = getattr(rt, "config", None) or {}
    return Path(cfg.get("buckets_dir") or "buckets").resolve()


def journal_root() -> Path:
    return _buckets_dir() / "journals"


def _now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def _parse_dt(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return _now()
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return datetime.fromisoformat(raw + "T00:00:00").astimezone()
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt.astimezone().replace(microsecond=0)
    except ValueError:
        return _now()


def _parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        parts = value
    else:
        parts = re.split(r"[,，\n]+", str(value))
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _clamp_importance(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DEFAULT_IMPORTANCE
    return max(1, min(10, n))


def _slugify(value: str) -> str:
    text = re.sub(r"\s+", "-", (value or "").strip().lower())
    text = re.sub(r"[^a-z0-9._-]+", "-", text).strip("._-")
    text = re.sub(r"-{2,}", "-", text)
    return (text[:48].strip("-") or "journal")


def _title_from_content(content: str) -> str:
    first = ""
    for line in (content or "").splitlines():
        line = line.strip(" #\t\r\n")
        if line:
            first = line
            break
    if not first:
        return "未命名日记"
    first = re.sub(r"\s+", " ", first)
    return first[:80]


def _path_for(created: datetime, slug: str, seq: int) -> Path:
    return journal_root() / created.strftime("%Y") / created.strftime("%m") / f"{created.strftime('%Y%m%d-%H%M%S')}-{slug}-{seq:03d}.md"


def _dump_frontmatter(metadata: dict[str, Any], content: str) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    lines.append(content.rstrip() + "\n")
    return "\n".join(lines)


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if raw == "":
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if raw.lower() == "true":
            return True
        if raw.lower() == "false":
            return False
        return raw.strip('"').strip("'")


def _load_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw_meta = text[4:end].strip().splitlines()
    content = text[end + 5 :]
    if content.startswith("\n"):
        content = content[1:]
    meta: dict[str, Any] = {}
    for line in raw_meta:
        if not line.strip() or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = _parse_scalar(value)
    return meta, content


def _read_post(path: Path) -> dict[str, Any]:
    meta, content = _load_markdown(path)
    meta["path"] = str(path)
    meta["file_name"] = path.name
    return {"metadata": meta, "content": content or "", "path": str(path)}


def _safe_journal_files() -> list[Path]:
    root = journal_root()
    if not root.exists():
        return []
    trash = (root / "_trash").resolve()
    files: list[Path] = []
    for path in root.rglob("*.md"):
        try:
            resolved = path.resolve()
            if trash == resolved or trash in resolved.parents:
                continue
            files.append(path)
        except OSError:
            continue
    return files


def _summary(content: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", (content or "").strip())
    return text[:limit] + ("…" if len(text) > limit else "")


def _entry_to_result(entry: dict[str, Any], include_full: bool = False) -> dict[str, Any]:
    meta = entry["metadata"]
    content = entry["content"]
    item = {
        "journal_id": meta.get("journal_id", ""),
        "entry_type": meta.get("entry_type", "free"),
        "title": meta.get("title", ""),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "author": meta.get("author", ""),
        "source": meta.get("source", ""),
        "tags": meta.get("tags") or [],
        "domain": meta.get("domain") or [],
        "related_buckets": meta.get("related_buckets") or [],
        "related_journals": meta.get("related_journals") or [],
        "importance": meta.get("importance", DEFAULT_IMPORTANCE),
        "mood": meta.get("mood", ""),
        "status": meta.get("status", "active"),
        "summary": _summary(content),
        "path": entry["path"],
        "file_name": meta.get("file_name", ""),
    }
    if include_full:
        item["content"] = content
        item["frontmatter"] = {k: v for k, v in meta.items() if k not in {"path", "file_name"}}
    return item


def _matches_date(created_at: str, date_from: str, date_to: str) -> bool:
    date_part = (created_at or "")[:10]
    if date_from and date_part < date_from:
        return False
    if date_to and date_part > date_to:
        return False
    return True


async def journal_write(
    content: str,
    title: Optional[str] = "",
    entry_type: Optional[str] = "free",
    tags: Optional[Any] = "",
    domain: Optional[Any] = "",
    importance: Optional[int] = DEFAULT_IMPORTANCE,
    author: Optional[str] = "agent",
    related_buckets: Optional[Any] = "",
    related_journals: Optional[Any] = "",
    mood: Optional[str] = "",
    date: Optional[str] = "",
    status: Optional[str] = "active",
    source: Optional[str] = "mcp",
) -> dict[str, Any]:
    body = (content or "").strip()
    if not body:
        raise ValueError("journal content required")

    kind = str(entry_type or "free").strip().lower()
    if kind not in ENTRY_TYPES:
        kind = "free"
    state = str(status or "active").strip().lower()
    if state not in STATUSES:
        state = "active"

    created = _parse_dt(date)
    title_text = (title or "").strip()[:120] or _title_from_content(body)
    slug = _slugify(title_text)
    root = journal_root()
    root.mkdir(parents=True, exist_ok=True)

    seq = 1
    path = _path_for(created, slug, seq)
    while path.exists():
        seq += 1
        path = _path_for(created, slug, seq)
    path.parent.mkdir(parents=True, exist_ok=True)

    journal_id = f"journal-{created.strftime('%Y%m%d-%H%M%S')}-{seq:03d}"
    iso = created.isoformat(timespec="seconds")
    metadata = {
        "journal_id": journal_id,
        "entry_type": kind,
        "title": title_text,
        "created_at": iso,
        "updated_at": iso,
        "author": (author or "agent").strip()[:60] or "agent",
        "source": (source or "mcp").strip()[:60] or "mcp",
        "tags": _parse_list(tags),
        "domain": _parse_list(domain),
        "related_buckets": _parse_list(related_buckets),
        "related_journals": _parse_list(related_journals),
        "importance": _clamp_importance(importance),
        "mood": (mood or "").strip()[:120],
        "status": state,
    }
    with path.open("w", encoding="utf-8") as f:
        f.write(_dump_frontmatter(metadata, body))
    return {"journal_id": journal_id, "path": str(path), "title": title_text, "entry_type": kind}


async def journal_read(
    query: Optional[str] = "",
    entry_type: Optional[str] = "",
    date_from: Optional[str] = "",
    date_to: Optional[str] = "",
    tags: Optional[Any] = "",
    domain: Optional[Any] = "",
    max_results: Optional[int] = 10,
    include_full: Optional[bool] = False,
) -> list[dict[str, Any]]:
    q = (query or "").strip().lower()
    kind = (entry_type or "").strip().lower()
    if kind and kind not in ENTRY_TYPES:
        return []
    tag_filter = {t.lower() for t in _parse_list(tags)}
    domain_filter = {d.lower() for d in _parse_list(domain)}
    try:
        limit = max(1, min(50, int(max_results or 10)))
    except (TypeError, ValueError):
        limit = 10
    full = bool(include_full)

    results: list[dict[str, Any]] = []
    for path in _safe_journal_files():
        try:
            entry = _read_post(path)
        except Exception:
            continue
        meta = entry["metadata"]
        if kind and meta.get("entry_type") != kind:
            continue
        if not _matches_date(str(meta.get("created_at", "")), date_from or "", date_to or ""):
            continue
        entry_tags = {str(t).lower() for t in (meta.get("tags") or [])}
        entry_domains = {str(d).lower() for d in (meta.get("domain") or [])}
        if tag_filter and not tag_filter.issubset(entry_tags):
            continue
        if domain_filter and not domain_filter.issubset(entry_domains):
            continue
        haystack = " ".join([
            str(meta.get("title", "")),
            " ".join(meta.get("tags") or []),
            " ".join(meta.get("domain") or []),
            entry["content"],
        ]).lower()
        if q and q not in haystack:
            continue
        results.append(_entry_to_result(entry, include_full=full))

    results.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return results[:limit]


async def journal_read_json(**kwargs: Any) -> str:
    items = await journal_read(**kwargs)
    return json.dumps({"ok": True, "journals": items, "total": len(items)}, ensure_ascii=False, indent=2)


async def move_journal_to_trash(journal_id: str) -> Optional[str]:
    if not JOURNAL_ID_RE.match(journal_id or ""):
        return None
    for path in _safe_journal_files():
        try:
            entry = _read_post(path)
        except Exception:
            continue
        if entry["metadata"].get("journal_id") != journal_id:
            continue
        trash = journal_root() / "_trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / path.name
        if target.exists():
            target = trash / f"{journal_id}-{path.name}"
        shutil.move(str(path), str(target))
        return str(target)
    return None
