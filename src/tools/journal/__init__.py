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
UNSET = object()
PROTECTED_UPDATE_FIELDS = {
    "journal_id",
    "created_at",
    "source",
    "created_by",
    "status",
    "updated_at",
    "updated_by",
    "history",
    "content",
    "path",
    "file_path",
    "relative_path",
    "detail_key",
    "file_name",
}


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


def _atomic_write(path: Path, metadata: dict[str, Any], content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_dump_frontmatter(metadata, content), encoding="utf-8")
    tmp.replace(path)


def _project_root() -> Path:
    return _buckets_dir().parent


def _backup_journal_file(path: Path, operation: str = "journal_update") -> str:
    stamp = _now().strftime("%Y%m%d_%H%M%S")
    safe_operation = re.sub(r"[^a-zA-Z0-9_-]+", "_", operation or "journal_update").strip("_")
    backup_dir = _project_root() / "backups" / f"{safe_operation}_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / path.name
    seq = 1
    while target.exists():
        target = backup_dir / f"{path.stem}-{stamp}-{seq:03d}{path.suffix}"
        seq += 1
    shutil.copy2(path, target)
    return str(target)


def _unique_trash_path(path: Path, journal_id: str) -> Path:
    trash = journal_root() / "_trash"
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / path.name
    seq = 1
    while target.exists():
        target = trash / f"{path.stem}-{journal_id}-{seq:03d}{path.suffix}"
        seq += 1
    return target


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(journal_root().resolve()).as_posix()
    except ValueError:
        return path.name


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


def _all_journal_files() -> list[Path]:
    root = journal_root()
    if not root.exists():
        return []
    files: list[Path] = []
    for path in root.rglob("*.md"):
        try:
            if path.resolve().is_file():
                files.append(path)
        except OSError:
            continue
    return files


def _journal_id_exists(journal_id: str) -> bool:
    for path in _all_journal_files():
        try:
            entry = _read_post(path)
        except Exception:
            continue
        if entry["metadata"].get("journal_id") == journal_id:
            return True
    return False


def find_journal_file(identifier: str) -> Optional[Path]:
    raw = str(identifier or "").strip()
    if not raw:
        return None
    root = journal_root().resolve()

    # 1) Relative path or absolute file path, constrained to buckets/journals.
    candidate: Optional[Path] = None
    try:
        raw_path = Path(raw)
        if raw_path.is_absolute():
            candidate = raw_path.resolve()
        elif "/" in raw or "\\" in raw:
            candidate = (root / raw.replace("\\", "/")).resolve()
        if candidate and candidate.suffix == ".md" and candidate.exists() and candidate.is_file():
            if candidate == root or root not in candidate.parents:
                return None
            trash = (root / "_trash").resolve()
            if trash == candidate or trash in candidate.parents:
                return None
            return candidate
    except OSError:
        return None

    # 2) journal_id from frontmatter.
    if JOURNAL_ID_RE.match(raw):
        for path in _safe_journal_files():
            try:
                entry = _read_post(path)
            except Exception:
                continue
            if entry["metadata"].get("journal_id") == raw:
                return path

    # 3) file_name fallback.
    if raw.endswith(".md") and "/" not in raw and "\\" not in raw:
        for path in _safe_journal_files():
            if path.name == raw:
                return path
    return None


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
        "updated_by": meta.get("updated_by", ""),
        "author": meta.get("author", ""),
        "source": meta.get("source", ""),
        "tags": meta.get("tags") or [],
        "domain": meta.get("domain") or [],
        "related_buckets": meta.get("related_buckets") or [],
        "related_journals": meta.get("related_journals") or [],
        "importance": meta.get("importance", DEFAULT_IMPORTANCE),
        "mood": meta.get("mood", ""),
        "status": meta.get("status", "active"),
        "history": meta.get("history") if isinstance(meta.get("history"), list) else [],
        "summary": _summary(content),
        "path": entry["path"],
        "file_path": entry["path"],
        "relative_path": _relative_path(Path(entry["path"])),
        "detail_key": _relative_path(Path(entry["path"])),
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
    while True:
        path = _path_for(created, slug, seq)
        journal_id = f"journal-{created.strftime('%Y%m%d-%H%M%S')}-{seq:03d}"
        if not path.exists() and not _journal_id_exists(journal_id):
            break
        seq += 1
    path.parent.mkdir(parents=True, exist_ok=True)

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


def _history_preview(content: str, limit: int = 400) -> str:
    text = re.sub(r"\s+", " ", (content or "").strip())
    return text[:limit] + ("..." if len(text) > limit else "")


def _normalize_update_type(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in ENTRY_TYPES:
        raise ValueError("journal_type must be handoff, event, or free")
    return kind


async def journal_update(
    journal_id: str,
    title: Any = UNSET,
    content: Any = UNSET,
    journal_type: Any = UNSET,
    tags: Any = UNSET,
    metadata: Optional[dict[str, Any]] = None,
    updated_by: Optional[str] = "unknown",
    update_note: Optional[str] = "",
) -> dict[str, Any]:
    jid = str(journal_id or "").strip()
    if not JOURNAL_ID_RE.match(jid):
        raise ValueError("invalid journal_id")
    path = find_journal_file(jid)
    if not path:
        raise FileNotFoundError(f"journal not found: {jid}")

    root = journal_root().resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("journal path is outside journals directory")
    trash = (root / "_trash").resolve()
    if trash == resolved or trash in resolved.parents:
        raise PermissionError("journals in _trash cannot be updated")

    old_meta, old_content = _load_markdown(path)
    if str(old_meta.get("journal_id") or "") != jid:
        raise ValueError("journal_id mismatch")
    if str(old_meta.get("status", "active")).strip().lower() != "active":
        raise PermissionError("only active journals can be updated")

    new_meta = dict(old_meta)
    new_content = old_content
    changed_fields: list[str] = []

    if title is not UNSET:
        next_title = str(title or "")[:120]
        if next_title != str(old_meta.get("title", "")):
            new_meta["title"] = next_title
            changed_fields.append("title")

    if journal_type is not UNSET:
        next_type = _normalize_update_type(journal_type)
        if next_type != str(old_meta.get("entry_type", "free")):
            new_meta["entry_type"] = next_type
            changed_fields.append("journal_type")

    if tags is not UNSET:
        next_tags = _parse_list(tags)
        if next_tags != (old_meta.get("tags") or []):
            new_meta["tags"] = next_tags
            changed_fields.append("tags")

    if content is not UNSET:
        next_content = str(content or "")
        if next_content != old_content:
            new_content = next_content
            changed_fields.append("content")

    if isinstance(metadata, dict):
        for key, value in metadata.items():
            clean_key = str(key or "").strip()
            if not clean_key or clean_key in PROTECTED_UPDATE_FIELDS:
                continue
            if clean_key == "entry_type":
                clean_key = "journal_type"
            if clean_key == "journal_type":
                next_value = _normalize_update_type(value)
                target_key = "entry_type"
            elif clean_key == "importance":
                next_value = _clamp_importance(value)
                target_key = clean_key
            elif clean_key in {"tags", "domain", "related_buckets", "related_journals"}:
                next_value = _parse_list(value)
                target_key = clean_key
            else:
                next_value = value
                target_key = clean_key
            if new_meta.get(target_key) != next_value:
                new_meta[target_key] = next_value
                changed_fields.append(target_key)

    changed_fields = list(dict.fromkeys(changed_fields))
    if not changed_fields:
        return {
            "ok": True,
            "journal_id": jid,
            "updated_at": old_meta.get("updated_at", ""),
            "updated_by": old_meta.get("updated_by", ""),
            "changed_fields": [],
            "path": str(path),
            "file_path": str(path),
            "relative_path": _relative_path(path),
            "backup_path": "",
        }

    actor = str(updated_by or "unknown").strip()[:80] or "unknown"
    updated_at = _now().isoformat(timespec="seconds")
    history = old_meta.get("history")
    if not isinstance(history, list):
        history = []
    history.append({
        "updated_at": updated_at,
        "updated_by": actor,
        "update_note": str(update_note or "")[:500],
        "changed_fields": changed_fields,
        "previous_snapshot": {
            "title": old_meta.get("title", ""),
            "journal_type": old_meta.get("entry_type", "free"),
            "tags": old_meta.get("tags") or [],
            "content_preview": _history_preview(old_content),
        },
    })
    new_meta["history"] = history[-20:]
    new_meta["updated_at"] = updated_at
    new_meta["updated_by"] = actor
    new_meta["created_at"] = old_meta.get("created_at", "")
    new_meta["journal_id"] = jid
    if "source" in old_meta:
        new_meta["source"] = old_meta.get("source")

    backup_path = _backup_journal_file(path)
    _atomic_write(path, new_meta, new_content)
    return {
        "ok": True,
        "journal_id": jid,
        "updated_at": updated_at,
        "updated_by": actor,
        "changed_fields": changed_fields,
        "path": str(path),
        "file_path": str(path),
        "relative_path": _relative_path(path),
        "backup_path": backup_path,
        "title": new_meta.get("title", ""),
        "entry_type": new_meta.get("entry_type", "free"),
    }


async def journal_update_json(**kwargs: Any) -> str:
    result = await journal_update(**kwargs)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def journal_delete(
    journal_id: str,
    deleted_by: Optional[str] = "unknown",
    delete_note: Optional[str] = "",
) -> dict[str, Any]:
    jid = str(journal_id or "").strip()
    if not JOURNAL_ID_RE.match(jid):
        raise ValueError("invalid journal_id")
    path = find_journal_file(jid)
    if not path:
        raise FileNotFoundError(f"journal not found: {jid}")

    root = journal_root().resolve()
    resolved = path.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("journal path is outside journals directory")
    trash = (root / "_trash").resolve()
    if trash == resolved or trash in resolved.parents:
        raise PermissionError("journals in _trash cannot be deleted again")

    old_meta, old_content = _load_markdown(path)
    if str(old_meta.get("journal_id") or "") != jid:
        raise ValueError("journal_id mismatch")
    if str(old_meta.get("status", "active")).strip().lower() != "active":
        raise PermissionError("only active journals can be deleted")

    actor = str(deleted_by or "unknown").strip()[:80] or "unknown"
    deleted_at = _now().isoformat(timespec="seconds")
    new_meta = dict(old_meta)
    history = old_meta.get("history")
    if not isinstance(history, list):
        history = []
    history.append({
        "updated_at": deleted_at,
        "updated_by": actor,
        "update_note": "delete: " + str(delete_note or "")[:490],
        "changed_fields": ["status"],
        "previous_snapshot": {
            "title": old_meta.get("title", ""),
            "journal_type": old_meta.get("entry_type", "free"),
            "tags": old_meta.get("tags") or [],
            "content_preview": _history_preview(old_content),
        },
    })
    new_meta["history"] = history[-20:]
    new_meta["updated_at"] = deleted_at
    new_meta["updated_by"] = actor
    new_meta["deleted_at"] = deleted_at
    new_meta["deleted_by"] = actor
    new_meta["delete_note"] = str(delete_note or "")[:500]
    new_meta["status"] = "trashed"
    new_meta["created_at"] = old_meta.get("created_at", "")
    new_meta["journal_id"] = jid
    if "source" in old_meta:
        new_meta["source"] = old_meta.get("source")

    backup_path = _backup_journal_file(path, "journal_delete")
    _atomic_write(path, new_meta, old_content)
    target = _unique_trash_path(path, jid)
    shutil.move(str(path), str(target))
    return {
        "ok": True,
        "journal_id": jid,
        "deleted_at": deleted_at,
        "deleted_by": actor,
        "trash_path": str(target),
        "backup_path": backup_path,
    }


async def journal_delete_json(**kwargs: Any) -> str:
    result = await journal_delete(**kwargs)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def journal_detail(identifier: str) -> Optional[dict[str, Any]]:
    path = find_journal_file(identifier)
    if not path:
        return None
    entry = _read_post(path)
    return _entry_to_result(entry, include_full=True)


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
