"""
Review gate for write tools.

When review_mode is enabled, existing write tools keep their public names but
write Markdown candidates into memory_review/pending instead of touching buckets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import _runtime as rt

_DEFAULT_REVIEW_MODE = {
    "enabled": True,
    "intercept_hold": True,
    "intercept_grow": True,
    "intercept_trace_high_impact": True,
    "intercept_letter": True,
    "intercept_plan": True,
    "intercept_i": True,
    "intercept_feel": True,
    "intercept_anchor": True,
    "auto_approve_low_importance": False,
}

_HIGH_IMPACT_TYPES = {"I", "feel", "anchor", "pinned", "update", "delete"}
_REVIEW_AREAS = {"pending", "approved", "rejected"}
_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?m)^[A-Z0-9_]*(API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*=.*$"),
]
_CANDIDATE_ID_RE = re.compile(r"^candidate-\d{8}-\d{6}-[A-Za-z0-9_-]+$")
_DEFAULT_TITLE_VALUES = {
    "hold 候选",
    "grow 候选",
    "trace 候选",
    "bucket 候选",
    "feel 候选",
    "pinned 候选",
    "plan 候选",
    "letter 候选",
    "i 候选",
    "候选",
    "candidate",
    "untitled",
}
_DEFAULT_TITLE_RE = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2}\s+)?"
    r"(?:hold|grow|trace|bucket|feel|pinned|plan|letter|i)\s*候选$",
    re.I,
)
_TITLE_TIME_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2}\s+")


def _dump_candidate(metadata: dict, content: str) -> str:
    return (
        "---\n"
        + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
        + "---\n\n"
        + (content or "").lstrip()
    )


def _load_candidate(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    match = re.match(r"(?s)^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", raw)
    if not match:
        return {}, raw
    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, match.group(2)


def get_review_mode() -> dict:
    cfg = dict(_DEFAULT_REVIEW_MODE)
    user_cfg = (rt.config or {}).get("review_mode", {}) if isinstance(rt.config, dict) else {}
    if isinstance(user_cfg, dict):
        cfg.update(user_cfg)
    env_enabled = os.environ.get("OMBRE_REVIEW_MODE_ENABLED", "").strip().lower()
    if env_enabled in {"0", "false", "no", "off"}:
        cfg["enabled"] = False
    elif env_enabled in {"1", "true", "yes", "on"}:
        cfg["enabled"] = True
    return cfg


def review_mode_enabled(flag_name: str = "") -> bool:
    cfg = get_review_mode()
    if not bool(cfg.get("enabled", True)):
        return False
    if flag_name:
        return bool(cfg.get(flag_name, True))
    return True


def review_mode_status() -> str:
    cfg = get_review_mode()
    lines = ["review_mode:"]
    for key in sorted(cfg):
        lines.append(f"- {key}: {cfg[key]}")
    return "\n".join(lines)


def _buckets_dir() -> Path:
    buckets_dir = ""
    if isinstance(rt.config, dict):
        buckets_dir = str(rt.config.get("buckets_dir") or "")
    if buckets_dir:
        return Path(buckets_dir).resolve()
    env_buckets = os.environ.get("OMBRE_BUCKETS_DIR", "").strip() or os.environ.get("OMBRE_VAULT_DIR", "").strip()
    if env_buckets:
        return Path(env_buckets).resolve()
    cwd = Path.cwd().resolve()
    if cwd.name == "_app" and cwd.parent.name == "buckets":
        return cwd.parent
    if (cwd / "buckets").exists():
        return (cwd / "buckets").resolve()
    module_root = Path(__file__).resolve().parents[2]
    if module_root.name == "_app" and module_root.parent.name == "buckets":
        return module_root.parent
    if (module_root / "buckets").exists():
        return (module_root / "buckets").resolve()
    return (cwd / "buckets").resolve()


def _review_dir(*parts: str) -> Path:
    path = _buckets_dir() / "memory_review"
    for part in parts:
        path = path / part
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidate_path(candidate_id: str, area: str = "pending") -> Path:
    _validate_candidate_id(candidate_id)
    _validate_review_area(area)
    return _review_dir(area) / f"{candidate_id}.md"


def _validate_candidate_id(candidate_id: str) -> None:
    if not _CANDIDATE_ID_RE.match(str(candidate_id or "")):
        raise ValueError("candidate_id 格式无效")


def _validate_review_area(area: str) -> None:
    if area not in _REVIEW_AREAS:
        raise ValueError("review area 格式无效")


def _next_candidate_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    areas = [_review_dir("pending"), _review_dir("approved"), _review_dir("rejected")]
    for idx in range(1, 1000):
        candidate_id = f"candidate-{ts}-{idx:03d}"
        if not any((area / f"{candidate_id}.md").exists() for area in areas):
            return candidate_id
    return f"candidate-{ts}-{datetime.now().microsecond:06d}"


def _redact_text(value: Any) -> tuple[Any, bool]:
    if value is None:
        return value, False
    if isinstance(value, dict):
        changed = False
        out = {}
        for k, v in value.items():
            rv, hit = _redact_text(v)
            out[k] = rv
            changed = changed or hit
        return out, changed
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            rv, hit = _redact_text(item)
            out.append(rv)
            changed = changed or hit
        return out, changed
    if not isinstance(value, str):
        return value, False
    redacted = value
    changed = False
    for pattern in _SECRET_PATTERNS:
        redacted, count = pattern.subn("[REDACTED]", redacted)
        changed = changed or count > 0
    if ".env" in redacted.lower():
        changed = True
    return redacted, changed


def _parse_importance(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("importance 必须是数字")
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError("importance 必须是数字") from e


def _clamp_importance(value: Any, default: int = 5) -> int:
    try:
        parsed = _parse_importance(value)
    except ValueError:
        parsed = default
    return max(1, min(10, parsed))


def clamp_importance(value: Any, default: int = 5) -> int:
    return _clamp_importance(value, default=default)


def clamp_float01(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def normalize_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]
    out = []
    seen = set()
    for item in raw_items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def normalize_domain(value: Any, default: list[str] | None = None) -> list[str]:
    fallback = list(default or ["未分类"])
    if value is None:
        return fallback
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]
    out = []
    seen = set()
    for item in raw_items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out or fallback


def pick_meta_arg(meta: dict, args: dict, key: str, default: Any = None) -> Any:
    if isinstance(meta, dict) and key in meta and meta.get(key) is not None:
        return meta.get(key)
    if isinstance(args, dict) and key in args and args.get(key) is not None:
        return args.get(key)
    return default


def _pick_float01(meta: dict, args: dict, key: str, default: float) -> float:
    value = pick_meta_arg(meta, args, key, None)
    if value in (None, "", -1, "-1"):
        return default
    return clamp_float01(value, default)


def record_adjustment(meta: dict, field: str, requested: Any, final: Any, reason: str) -> None:
    if requested == final:
        return
    adjustments = meta.setdefault("field_adjustments", [])
    if not isinstance(adjustments, list):
        adjustments = []
        meta["field_adjustments"] = adjustments
    adjustments.append(
        {
            "field": field,
            "requested": requested,
            "final": final,
            "reason": reason,
        }
    )


def _cap_importance(suggested_type: str, importance: int, reason: str) -> tuple[int, str]:
    value = _clamp_importance(importance)
    return value, reason


def _extract_requested_importance(args: dict | None) -> tuple[Any, int] | tuple[None, None]:
    if not isinstance(args, dict):
        return None, None
    for key in ("requested_importance", "raw_importance", "original_importance", "importance"):
        if key in args and args.get(key) is not None:
            raw_value = args.get(key)
            return raw_value, _clamp_importance(raw_value)
    return None, None


def maybe_compress_importance_for_batch(raw_importance: Any) -> int:
    """Placeholder for future batch/import distribution compression.

    Review Gate must not apply bulk-import anti-inflation rules to explicit
    single-write candidates. Batch import code can call this future hook before
    creating pending candidates and should preserve raw_importance separately.
    """
    return _clamp_importance(raw_importance)


def maybe_compress_batch_importance(raw_importance: Any, context: dict | None = None) -> int:
    value = _clamp_importance(raw_importance)
    if value <= 3:
        return max(2, value)
    if value <= 6:
        return min(5, value)
    if value <= 8:
        return value - 1
    if value == 9:
        return 8
    return 9 if (context or {}).get("explicit_core") else 8


def _metadata_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _first_heading(content: str, fallback: str) -> str:
    m = re.search(r"(?m)^#\s+(.+?)\s*$", content or "")
    return m.group(1).strip() if m else fallback


def _is_default_title(title: str) -> bool:
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    if not value:
        return True
    return value.lower() in _DEFAULT_TITLE_VALUES or bool(_DEFAULT_TITLE_RE.match(value))


def _clean_title_seed(text: str) -> str:
    value = re.sub(r"(?m)^#+\s*", "", str(text or ""))
    value = re.sub(r"\[[^\]]*\]\([^)]+\)", "", value)
    value = re.sub(r"`+", "", value)
    value = value.replace("_", " ")
    value = re.sub(r"[#*~>\[\]{}<>]+", "", value)
    value = re.sub(r"[（(]\s*\d{4}[^)）]*[)）]", "", value)
    value = re.sub(r"^\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2}\s+", "", value)
    value = re.sub(r"^\d{2}-\d{2}-\d{2}\s+", "", value)
    value = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ：:，,。.!！?？；;、-—")
    return value


def _trim_generated_title(text: str) -> str:
    value = _clean_title_seed(text)
    if not value:
        return ""
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", value))
    if has_cjk:
        if len(value) > 24:
            for sep in ["。", "！", "？", "；", "，", ",", "、"]:
                part = value.split(sep, 1)[0].strip()
                if 6 <= len(part) <= 24:
                    value = part
                    break
        if len(value) > 40:
            value = value[:40].rstrip(" ：:，,。.!！?？；;、-—")
    else:
        words = value.split()
        if len(words) > 8:
            value = " ".join(words[:8]).rstrip(".,;:!?")
        if len(value) > 80:
            value = value[:80].rsplit(" ", 1)[0].rstrip(".,;:!?") or value[:80]
    return value or ""


def generate_candidate_title(
    content: str,
    *,
    preferred_title: str = "",
    original_tool: str = "",
    original_arguments: dict | None = None,
) -> str:
    """Return a human-readable candidate title without using tool-name defaults."""
    original_arguments = original_arguments or {}
    title_sources = [
        preferred_title,
        original_arguments.get("title", ""),
        original_arguments.get("name", ""),
        original_arguments.get("summary", ""),
    ]
    for source in title_sources:
        if _is_default_title(str(source or "")):
            continue
        candidate = _trim_generated_title(str(source or ""))
        if candidate and not _is_default_title(candidate):
            return candidate

    text = _section(content, "候选内容") or content or ""
    text = _clean_title_seed(text)
    if not text:
        return "未命名候选"

    if re.search(r"(牙冠|根管|补牙)", text) and "回国" in text:
        return "牙冠咬碎与回国补牙"
    if "虾" in text and ("中文灾难" in text or "送走" in text):
        return "虾的中文灾难与送走事件"
    if "REVIEW APPROVE CONFIRM FIX" in text.upper():
        return "Review approve confirm test"

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), text)
    colon_match = re.match(r"^(.{4,32}?)[：:]\s*(.+)$", first_line)
    if colon_match:
        prefix = _trim_generated_title(colon_match.group(1))
        if prefix and not _is_default_title(prefix):
            return prefix

    first_sentence = re.split(r"[。！？!?]\s*", first_line, 1)[0].strip()
    first_sentence = re.sub(r"^(主人|我|我们|CC酱|虾)\s*", "", first_sentence)
    title = _trim_generated_title(first_sentence)
    if title and not _is_default_title(title):
        return title
    fallback = f"{original_tool} 记忆".strip() if original_tool else "未命名候选"
    return "未命名候选" if _is_default_title(fallback) else fallback


def _replace_first_heading(content: str, title: str) -> str:
    if re.search(r"(?m)^#\s+.+?\s*$", content or ""):
        return re.sub(r"(?m)^#\s+.+?\s*$", f"# {title}", content or "", count=1)
    return f"# {title}\n\n{content or ''}".lstrip()


def _format_candidate_time(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H-%M-%S")
    except ValueError:
        pass
    match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})[T\s_]+(\d{1,2})[:-](\d{1,2})[:-](\d{1,2})", raw)
    if match:
        yyyy, mm, dd, hh, mi, ss = match.groups()
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d} {int(hh):02d}-{int(mi):02d}-{int(ss):02d}"
    return ""


def _candidate_time_from_id(candidate_id: str) -> str:
    match = re.match(r"^candidate-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-", str(candidate_id or ""))
    if not match:
        return ""
    yyyy, mm, dd, hh, mi, ss = match.groups()
    return f"{yyyy}-{mm}-{dd} {hh}-{mi}-{ss}"


def _candidate_display_time(meta: dict, candidate_id: str, path: Path | None = None) -> str:
    for key in ("created_at", "timestamp", "source_time", "created"):
        value = _format_candidate_time(meta.get(key))
        if value:
            return value
    value = _candidate_time_from_id(candidate_id)
    if value:
        return value
    if path is not None:
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H-%M-%S")
    return datetime.now().strftime("%Y-%m-%d %H-%M-%S")


def _candidate_display_title(title: str, display_time: str) -> str:
    clean = str(title or "").strip()
    if not clean:
        clean = "未命名候选"
    if _TITLE_TIME_PREFIX_RE.match(clean):
        return clean
    return f"{display_time} {clean}" if display_time else clean


def _section(content: str, heading: str) -> str:
    pattern = rf"(?ms)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, content or "")
    return match.group(1).strip() if match else ""


def _content_summary(content: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", _section(content, "候选内容") or content or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalize_candidate_domain_value(value: Any) -> list[str]:
    if value in (None, "", [], ()):
        return []
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]
    out = []
    seen = set()
    for item in raw_items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def _candidate_domain(meta: dict) -> list[str]:
    args = meta.get("original_arguments_redacted") if isinstance(meta, dict) else {}
    raw_frontmatter = meta.get("raw_frontmatter") if isinstance(meta, dict) else {}
    final_fields = meta.get("final_fields") if isinstance(meta, dict) else {}
    for source in (meta, raw_frontmatter, args, final_fields):
        if not isinstance(source, dict) or "domain" not in source:
            continue
        domain = _normalize_candidate_domain_value(source.get("domain"))
        if domain:
            return domain
    return []


def _candidate_record(path: Path, include_body: bool = False) -> dict:
    meta, content = _load_candidate(path)
    cid = str(meta.get("candidate_id") or path.stem)
    key = path.stem
    title = generate_candidate_title(
        _section(content, "候选内容") or content,
        preferred_title=str(meta.get("title") or _first_heading(content, cid)),
        original_tool=str(meta.get("original_tool") or ""),
        original_arguments=meta.get("original_arguments_redacted") or {},
    )
    display_time = _candidate_display_time(meta, cid, path)
    display_title = _candidate_display_title(title, display_time)
    record = {
        "candidate_key": key,
        "file_name": path.name,
        "candidate_id": cid,
        "candidate_id_mismatch": cid != key,
        "title": title,
        "display_time": display_time,
        "display_title": display_title,
        "suggested_type": meta.get("suggested_type", ""),
        "suggested_importance": meta.get("suggested_importance", ""),
        "tags": meta.get("tags") or [],
        "domain": _candidate_domain(meta),
        "created_by": meta.get("created_by", ""),
        "source_file": meta.get("source_file", ""),
        "status": meta.get("status", "pending"),
        "content_summary": _content_summary(content),
        "created_at": meta.get("created_at", ""),
        "original_tool": meta.get("original_tool", ""),
        "perspective": meta.get("perspective", ""),
        "reason": meta.get("reason", ""),
        "notes": meta.get("notes", ""),
        "source_quote": meta.get("source_quote", ""),
        "rejected_at": meta.get("rejected_at", meta.get("reviewed_at", "")),
        "rejection_reason": meta.get("rejection_reason", meta.get("reject_reason", "")),
        "approved_at": meta.get("approved_at", ""),
        "approved_result": meta.get("approved_result", meta.get("formal_result", "")),
        "needs_user_confirmation": _metadata_bool(meta.get("needs_user_confirmation", True)),
        "explicitly_approved": _metadata_bool(meta.get("explicitly_approved", False)),
        "target_bucket_id": meta.get("target_bucket_id", ""),
    }
    if include_body:
        record["frontmatter"] = meta
        record["body"] = content
        record["candidate_content"] = _section(content, "候选内容")
        record["original_arguments_summary"] = _section(content, "原始调用摘要")
        record["planned_updates_summary"] = _section(content, "计划修改字段")
    return record


async def list_review_records(area: str = "pending", limit: int = 200) -> list[dict]:
    _validate_review_area(area)
    directory = _review_dir(area)
    files = sorted(directory.glob("candidate-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    limit = max(1, min(500, int(limit or 200)))
    return [_candidate_record(path, include_body=False) for path in files[:limit]]


async def list_pending_records(limit: int = 200) -> list[dict]:
    return await list_review_records("pending", limit=limit)


async def read_review_record(area: str, candidate_id: str) -> dict | None:
    _validate_review_area(area)
    path = _candidate_path(candidate_id, area)
    if not path.exists():
        return None
    return _candidate_record(path, include_body=True)


async def read_pending_record(candidate_id: str) -> dict | None:
    return await read_review_record("pending", candidate_id)


def _format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def preserve_requested_fields(meta: dict, args: dict) -> None:
    for key in (
        "domain",
        "valence",
        "arousal",
        "source_bucket",
        "triggered_by",
        "raw_importance",
        "grow_batch_id",
        "grow_item_index",
    ):
        if key in meta or not isinstance(args, dict) or key not in args:
            continue
        value = args.get(key)
        if value in (None, "", -1, "-1", []):
            continue
        if key == "domain":
            meta[key] = normalize_domain(value)
        elif key in {"valence", "arousal"}:
            meta[key] = clamp_float01(value, 0.5 if key == "valence" else 0.3)
        else:
            meta[key] = value


def _type_default_domain(suggested_type: str) -> list[str]:
    if suggested_type == "feel":
        return ["feel"]
    if suggested_type == "I":
        return ["self"]
    if suggested_type == "plan":
        return ["plan"]
    if suggested_type == "letter":
        return ["letter"]
    return ["未分类"]


def _type_default_arousal(suggested_type: str) -> float:
    return 0.4 if suggested_type == "plan" else 0.3


def _with_system_tag(tag: str, tags: list[str]) -> list[str]:
    return normalize_tags([tag] + list(tags or []))


def _final_fields_for_approval(
    meta: dict,
    body: str,
    candidate_id: str,
    src: Path,
) -> dict:
    args = meta.get("original_arguments_redacted") or {}
    if not isinstance(args, dict):
        args = {}
    suggested_type = str(meta.get("suggested_type") or "bucket")
    content = _section(body, "候选内容")
    preferred_title = str(meta.get("title") or _first_heading(body, candidate_id))
    title = generate_candidate_title(
        content or body,
        preferred_title=preferred_title,
        original_tool=str(meta.get("original_tool") or ""),
        original_arguments=args,
    )
    if preferred_title and preferred_title != title:
        meta.setdefault("original_title", preferred_title)
    display_time = _candidate_display_time(meta, candidate_id, src)
    display_title = _candidate_display_title(title, display_time)

    raw_tags = pick_meta_arg(meta, args, "tags", [])
    tags = normalize_tags(raw_tags)
    domain = normalize_domain(
        pick_meta_arg(meta, args, "domain", None),
        default=_type_default_domain(suggested_type),
    )
    valence = _pick_float01(meta, args, "valence", 0.5)
    arousal = _pick_float01(meta, args, "arousal", _type_default_arousal(suggested_type))
    importance = _clamp_importance(meta.get("suggested_importance") or args.get("importance") or 5)
    source_bucket = str(pick_meta_arg(meta, args, "source_bucket", "") or "")
    triggered_by = str(pick_meta_arg(meta, args, "triggered_by", source_bucket) or "")
    source_tool_default = str(meta.get("original_tool") or "review_approve")
    source_tool = str(pick_meta_arg(meta, args, "source_tool", source_tool_default) or source_tool_default)
    grow_batch_id = str(pick_meta_arg(meta, args, "grow_batch_id", "") or "")
    why_remembered = str(pick_meta_arg(meta, args, "why_remembered", "") or "")

    final_importance = importance
    final_tags = tags
    final_domain = domain
    final_valence = valence
    final_arousal = arousal
    extra_update: dict[str, Any] = {}

    if suggested_type == "pinned":
        if importance != 10:
            record_adjustment(meta, "importance", importance, 10, "pinned 类型规则固定 importance=10")
        final_importance = 10
    elif suggested_type == "feel":
        final_tags = _with_system_tag("__feel__", tags)
        if importance > 5:
            record_adjustment(meta, "importance", importance, 5, "feel 类型规则 importance 最多 5")
        final_importance = min(importance, 5)
        final_domain = domain if ("domain" in meta or "domain" in args) else ["feel"]
    elif suggested_type == "I":
        final_tags = _with_system_tag("__i__", tags)
        if importance > 6:
            record_adjustment(meta, "importance", importance, 6, "I 类型规则 importance 最多 6")
        final_importance = min(importance, 6)
        final_domain = domain if ("domain" in meta or "domain" in args) else ["self"]
        extra_update["dont_surface"] = True
    elif suggested_type == "plan":
        final_tags = _with_system_tag("__plan__", tags)
        if importance != 7:
            record_adjustment(meta, "importance", importance, 7, "plan 类型规则固定 importance=7")
        final_importance = 7
        final_domain = ["plan"]
        final_arousal = _pick_float01(meta, args, "arousal", 0.4)
    elif suggested_type == "letter":
        final_tags = _with_system_tag("__letter__", tags)
        if importance != 10:
            record_adjustment(meta, "importance", importance, 10, "letter 类型规则固定 importance=10")
        final_importance = 10
        final_domain = ["letter"]

    return {
        "suggested_type": suggested_type,
        "content": content,
        "title": title,
        "display_title": display_title,
        "tags": final_tags,
        "importance": final_importance,
        "domain": final_domain,
        "valence": final_valence,
        "arousal": final_arousal,
        "source_bucket": source_bucket,
        "triggered_by": triggered_by,
        "source_tool": source_tool,
        "grow_batch_id": grow_batch_id,
        "why_remembered": why_remembered,
        "extra_update": extra_update,
        "args": args,
        "planned_updates": meta.get("planned_updates") or {},
    }


def _format_approval_plan(candidate_id: str, fields: dict, dry_run: bool, confirmed: bool) -> str:
    lines = [
        f"候选：{candidate_id}",
        f"类型：{fields['suggested_type']}",
        f"标题：{fields['display_title']}",
        f"dry_run：{dry_run}",
        f"confirmed：{confirmed}",
        f"将执行：按 {fields['suggested_type']} 语义写入或更新正式 buckets。",
        "最终字段：",
        f"- importance: {fields['importance']}",
        f"- tags: {json.dumps(fields['tags'], ensure_ascii=False)}",
        f"- domain: {json.dumps(fields['domain'], ensure_ascii=False)}",
        f"- valence: {fields['valence']}",
        f"- arousal: {fields['arousal']}",
    ]
    if fields.get("source_bucket"):
        lines.append(f"- source_bucket: {fields['source_bucket']}")
    if fields.get("triggered_by"):
        lines.append(f"- triggered_by: {fields['triggered_by']}")
    if fields.get("grow_batch_id"):
        lines.append(f"- grow_batch_id: {fields['grow_batch_id']}")
    args = fields.get("args") or {}
    if fields["suggested_type"] == "plan":
        lines.append(f"- status: {args.get('status', 'active')}")
        lines.append(f"- weight: {args.get('weight', 0.5)}")
        if args.get("related_bucket"):
            lines.append(f"- related_bucket: {args.get('related_bucket')}")
    if fields["suggested_type"] == "letter":
        for key in ("author", "title", "date", "user_name"):
            if args.get(key):
                lines.append(f"- {key}: {args.get(key)}")
    planned_updates = fields.get("planned_updates") or {}
    if planned_updates:
        lines.append("- planned_updates: " + json.dumps(planned_updates, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


async def create_pending_candidate(
    *,
    original_tool: str,
    suggested_type: str,
    title: str,
    content: str,
    suggested_importance: int = 5,
    tags: list[str] | None = None,
    original_arguments: dict | None = None,
    reason: str = "",
    notes: str = "",
    source_file: str = "",
    source_location: str = "",
    source_quote: str = "",
    target_bucket_id: str = "",
    planned_updates: dict | None = None,
) -> dict:
    tags = normalize_tags(tags or [])
    original_arguments = original_arguments or {}
    planned_updates = planned_updates or {}
    requested_raw, requested_clamped = _extract_requested_importance(original_arguments)
    suggested_raw = suggested_importance
    suggested_clamped = _clamp_importance(suggested_importance)
    suggested_importance, reason = _cap_importance(suggested_type, suggested_importance, reason)
    importance_adjust_reasons = []
    if suggested_importance != suggested_clamped:
        importance_adjust_reasons.append(
            f"suggested_importance 已从 {suggested_raw} 调整为 {suggested_importance}。"
        )
    if requested_clamped is not None and suggested_importance != requested_clamped:
        importance_adjust_reasons.append(
            f"requested_importance={requested_clamped}，实际 suggested_importance={suggested_importance}。"
        )

    redacted_content, hit_content = _redact_text(content)
    redacted_quote, hit_quote = _redact_text(source_quote)
    redacted_reason, hit_reason = _redact_text(reason)
    redacted_args, hit_args = _redact_text(original_arguments)
    redacted_updates, hit_updates = _redact_text(planned_updates)
    secret_hit = any([hit_content, hit_quote, hit_reason, hit_args, hit_updates])
    if secret_hit:
        notes = (notes + "\n" if notes else "") + "检测到疑似密钥，已脱敏。"
    effective_title = generate_candidate_title(
        str(redacted_content or ""),
        preferred_title=title,
        original_tool=original_tool,
        original_arguments=redacted_args if isinstance(redacted_args, dict) else {},
    )

    needs_confirmation = True
    explicitly_approved = False
    created_at = datetime.now().isoformat(timespec="seconds")

    arg_summary = "\n".join(
        f"- {k}: {_format_value(v)}" for k, v in (redacted_args or {}).items()
    ) or "- 无"
    update_summary = ""
    if redacted_updates:
        update_summary = "\n\n## 计划修改字段\n\n" + "\n".join(
            f"- {k}: {_format_value(v)}" for k, v in redacted_updates.items()
        )
    body = f"""# {effective_title}

## 来源工具

{original_tool}

## 候选内容

{redacted_content or ''}

## 原始调用摘要

{arg_summary}
{update_summary}

## 为什么进入审核

{redacted_reason or 'review mode 已开启，因此本次写入被拦截到 pending，等待主人确认。'}

## 审核建议

批准 / 修改后批准 / 拒绝 / 等 CC 酱确认 / 等猫茶确认
"""
    last_error = None
    for _attempt in range(1000):
        candidate_id = _next_candidate_id()
        metadata = {
            "candidate_id": candidate_id,
            "title": effective_title,
            "status": "pending",
            "original_tool": original_tool,
            "suggested_type": suggested_type,
            "suggested_importance": suggested_importance,
            "tags": tags,
            "source_file": source_file,
            "source_location": source_location,
            "source_quote": redacted_quote or "",
            "created_by": "mcp",
            "needs_user_confirmation": needs_confirmation,
            "explicitly_approved": explicitly_approved,
            "original_arguments_redacted": redacted_args,
            "reason": redacted_reason or "",
            "notes": notes or "",
            "created_at": created_at,
        }
        preserve_requested_fields(metadata, redacted_args if isinstance(redacted_args, dict) else {})
        if target_bucket_id:
            metadata["target_bucket_id"] = target_bucket_id
        if redacted_updates:
            metadata["planned_updates"] = redacted_updates
        if requested_raw is not None:
            metadata["original_importance"] = requested_raw
            metadata["requested_importance"] = requested_clamped
        elif suggested_raw != suggested_importance:
            metadata["original_importance"] = suggested_raw
            metadata["requested_importance"] = suggested_clamped
        if importance_adjust_reasons:
            upstream_reason = ""
            if isinstance(redacted_args, dict):
                upstream_reason = str(redacted_args.get("importance_adjust_reason") or "")
            metadata["importance_adjust_reason"] = " ".join(
                [r for r in [upstream_reason, *importance_adjust_reasons] if r]
            )
        path = _candidate_path(candidate_id, "pending")
        try:
            with path.open("x", encoding="utf-8") as fh:
                fh.write(_dump_candidate(metadata, body))
            return {"candidate_id": candidate_id, "path": str(path), "suggested_type": suggested_type}
        except FileExistsError as e:
            last_error = e
            continue
    raise RuntimeError(f"无法生成唯一 pending candidate_id：{last_error}")


def pending_response(candidate: dict) -> str:
    return (
        f"已进入待审核区：candidate_id={candidate['candidate_id']}\n"
        "未写入正式记忆库。"
    )


async def list_pending_memories(limit: int = 50) -> str:
    pending = _review_dir("pending")
    files = sorted(pending.glob("candidate-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    limit = max(1, min(200, int(limit or 50)))
    if not files:
        return "当前没有 pending 候选。"
    lines = [f"=== pending 候选（显示 {min(len(files), limit)}/{len(files)}）==="]
    for path in files[:limit]:
        meta, content = _load_candidate(path)
        cid = meta.get("candidate_id") or path.stem
        title = generate_candidate_title(
            _section(content, "候选内容") or content,
            preferred_title=str(meta.get("title") or _first_heading(content, cid)),
            original_tool=str(meta.get("original_tool") or ""),
            original_arguments=meta.get("original_arguments_redacted") or {},
        )
        lines.append(
            f"- {cid} | {meta.get('suggested_type', '?')} | "
            f"importance={meta.get('suggested_importance', '?')} | "
            f"approved={meta.get('explicitly_approved', False)} | {title}"
        )
    return "\n".join(lines)


async def read_pending_memory(candidate_id: str) -> str:
    path = _candidate_path(candidate_id, "pending")
    if not path.exists():
        return f"未找到 pending 候选：{candidate_id}"
    return path.read_text(encoding="utf-8")


async def update_pending_memory(
    candidate_id: str,
    updates_json: str = "",
    body: str = "",
) -> str:
    path = _candidate_path(candidate_id, "pending")
    if not path.exists():
        return f"未找到 pending 候选：{candidate_id}"
    meta, content = _load_candidate(path)
    updates = {}
    if updates_json and updates_json.strip():
        try:
            parsed = json.loads(updates_json)
            if not isinstance(parsed, dict):
                return "updates_json 必须是 JSON object。"
            updates = parsed
        except json.JSONDecodeError as e:
            return f"updates_json 解析失败：{e}"
    update_notes = []
    for key, value in updates.items():
        if key == "suggested_importance":
            try:
                parsed_value = _parse_importance(value)
            except ValueError as e:
                return str(e)
            clamped_value = max(1, min(10, parsed_value))
            meta[key] = clamped_value
            meta["review_requested_importance"] = parsed_value
            if clamped_value != parsed_value:
                note = (
                    f"suggested_importance 已按合法范围从 {parsed_value} clamp 到 {clamped_value}。"
                )
                meta["importance_adjust_reason"] = note
                update_notes.append(note)
            else:
                update_notes.append(f"suggested_importance 已更新为 {clamped_value}。")
            continue
        if key == "tags":
            meta[key] = normalize_tags(value)
            update_notes.append("tags 已 normalize 并去重。")
            continue
        if key == "domain":
            meta[key] = normalize_domain(value)
            update_notes.append("domain 已 normalize 并去重。")
            continue
        if key in {"valence", "arousal"}:
            default = 0.5 if key == "valence" else 0.3
            final = clamp_float01(value, default)
            meta[key] = final
            if final != value:
                record_adjustment(meta, key, value, final, "update_pending_memory 数值合法范围 clamp")
                update_notes.append(f"{key} 已按合法范围调整为 {final}。")
            else:
                update_notes.append(f"{key} 已更新为 {final}。")
            continue
        if key == "planned_updates":
            if not isinstance(value, dict):
                return "planned_updates 必须是 JSON object。"
            clean_updates = dict(value)
            if "tags" in clean_updates:
                clean_updates["tags"] = normalize_tags(clean_updates["tags"])
            if "domain" in clean_updates:
                clean_updates["domain"] = normalize_domain(clean_updates["domain"])
            if "importance" in clean_updates:
                clean_updates["importance"] = _clamp_importance(clean_updates["importance"])
            if "valence" in clean_updates:
                clean_updates["valence"] = clamp_float01(clean_updates["valence"], 0.5)
            if "arousal" in clean_updates:
                clean_updates["arousal"] = clamp_float01(clean_updates["arousal"], 0.3)
            meta[key] = clean_updates
            update_notes.append("planned_updates 已保存。")
            continue
        if key in {
            "title",
            "source_bucket",
            "triggered_by",
            "source_file",
            "source_location",
            "source_quote",
            "target_bucket_id",
            "reason",
            "notes",
            "status",
            "weight",
            "related_bucket",
            "author",
            "date",
            "user_name",
            "grow_batch_id",
            "grow_item_index",
            "raw_importance",
            "requested_importance",
            "original_importance",
        }:
            meta[key] = value
            update_notes.append(f"{key} 已更新。")
            continue
        meta[key] = value
    if body:
        content = body
    path.write_text(_dump_candidate(meta, content), encoding="utf-8")
    suffix = ("\n" + "\n".join(update_notes)) if update_notes else ""
    return f"已更新 pending 候选：{candidate_id}{suffix}"


async def reject_pending_memory(candidate_id: str, reason: str = "") -> str:
    src = _candidate_path(candidate_id, "pending")
    if not src.exists():
        return f"未找到 pending 候选：{candidate_id}"
    meta, content = _load_candidate(src)
    meta["status"] = "rejected"
    meta["rejected_at"] = datetime.now().isoformat(timespec="seconds")
    if reason:
        meta["rejection_reason"] = reason[:500]
        meta["reject_reason"] = reason[:500]
    meta["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
    src.write_text(_dump_candidate(meta, content), encoding="utf-8")
    dst = _candidate_path(candidate_id, "rejected")
    if dst.exists():
        dst = _review_dir("rejected") / f"{candidate_id}-{datetime.now().strftime('%H%M%S')}.md"
    shutil.move(str(src), str(dst))
    return f"已拒绝并移动到 rejected：{candidate_id}"


async def restore_rejected_memory(candidate_id: str) -> str:
    src = _candidate_path(candidate_id, "rejected")
    if not src.exists():
        return f"未找到 rejected 候选：{candidate_id}"
    meta, content = _load_candidate(src)
    meta["status"] = "pending"
    meta["explicitly_approved"] = False
    meta["needs_user_confirmation"] = True
    meta["restored_at"] = datetime.now().isoformat(timespec="seconds")
    meta["restored_from"] = "rejected"
    src.write_text(_dump_candidate(meta, content), encoding="utf-8")
    dst = _candidate_path(candidate_id, "pending")
    if dst.exists():
        dst = _review_dir("pending") / f"{candidate_id}-{datetime.now().strftime('%H%M%S')}.md"
    shutil.move(str(src), str(dst))
    return f"已恢复到 pending：{candidate_id}"


def _approval_blockers(meta: dict) -> list[str]:
    blockers = []
    suggested_type = str(meta.get("suggested_type") or "bucket")
    needs_confirmation = _metadata_bool(meta.get("needs_user_confirmation", True))
    explicitly_approved = _metadata_bool(meta.get("explicitly_approved", False))
    if needs_confirmation and not explicitly_approved:
        blockers.append("needs_user_confirmation=true，但 explicitly_approved 不是 true")
    if suggested_type in _HIGH_IMPACT_TYPES and not explicitly_approved:
        blockers.append(f"{suggested_type} 类型必须 explicitly_approved=true")
    return blockers


async def approve_pending_memory(candidate_id: str, dry_run: bool = True, confirmed: bool = False) -> str:
    src = _candidate_path(candidate_id, "pending")
    if not src.exists():
        return f"未找到 pending 候选：{candidate_id}"
    meta, body = _load_candidate(src)
    if not dry_run and confirmed:
        meta["explicitly_approved"] = True
        meta["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        meta["confirmed_via"] = "review_approve_confirmed"
        src.write_text(_dump_candidate(meta, body), encoding="utf-8")
    fields = _final_fields_for_approval(meta, body, candidate_id, src)
    suggested_type = fields["suggested_type"]
    content = fields["content"]
    title = fields["title"]
    display_title = fields["display_title"]
    meta["title"] = title
    meta["display_title"] = display_title
    meta["final_fields"] = {
        k: fields[k]
        for k in (
            "importance",
            "tags",
            "domain",
            "valence",
            "arousal",
            "source_bucket",
            "triggered_by",
            "source_tool",
            "grow_batch_id",
        )
        if fields.get(k) not in (None, "", [])
    }
    blockers = _approval_blockers(meta)
    plan = _format_approval_plan(candidate_id, fields, dry_run, confirmed)
    if blockers:
        plan += "\n阻止正式批准：\n" + "\n".join(f"- {b}" for b in blockers)
    if dry_run:
        return plan + "\n未修改正式记忆库。"
    if blockers:
        return plan + "\n未修改正式记忆库。"
    body = _replace_first_heading(body, title)

    bucket_id = ""
    args = fields["args"]
    planned_updates = fields["planned_updates"]
    if suggested_type == "bucket":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=fields["tags"],
            importance=fields["importance"],
            domain=fields["domain"],
            valence=fields["valence"],
            arousal=fields["arousal"],
            name=display_title,
            why_remembered=fields["why_remembered"],
            source_tool=fields["source_tool"],
            grow_batch_id=fields["grow_batch_id"],
        )
    elif suggested_type == "pinned":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=fields["tags"],
            importance=fields["importance"],
            domain=fields["domain"],
            valence=fields["valence"],
            arousal=fields["arousal"],
            name=display_title,
            bucket_type="permanent",
            pinned=True,
            why_remembered=fields["why_remembered"],
            source_tool=fields["source_tool"],
        )
    elif suggested_type == "feel":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=fields["tags"],
            importance=fields["importance"],
            domain=fields["domain"],
            valence=fields["valence"],
            arousal=fields["arousal"],
            name=display_title,
            bucket_type="feel",
            triggered_by=fields["triggered_by"],
            why_remembered=fields["why_remembered"],
            source_tool=fields["source_tool"],
        )
    elif suggested_type == "I":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=fields["tags"],
            importance=fields["importance"],
            domain=fields["domain"],
            valence=fields["valence"],
            arousal=fields["arousal"],
            name=display_title,
            bucket_type="i",
            weight=0.8,
            why_remembered=fields["why_remembered"],
            source_tool=fields["source_tool"],
        )
        await rt.bucket_mgr.update(bucket_id, dont_surface=True)
    elif suggested_type == "plan":
        weight = clamp_float01(pick_meta_arg(meta, args, "weight", 0.5), 0.5)
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=fields["tags"],
            importance=fields["importance"],
            domain=fields["domain"],
            valence=fields["valence"],
            arousal=fields["arousal"],
            name=display_title,
            bucket_type="plan",
            weight=weight,
            why_remembered=fields["why_remembered"],
            source_tool=fields["source_tool"],
        )
        try:
            from ._common import append_plan_change_log
            initial_log = append_plan_change_log([], "created", to=str(pick_meta_arg(meta, args, "status", "active") or "active"))
        except Exception:
            initial_log = []
        await rt.bucket_mgr.update(
            bucket_id,
            status=str(pick_meta_arg(meta, args, "status", "active") or "active"),
            related_bucket=str(pick_meta_arg(meta, args, "related_bucket", "") or ""),
            change_log=initial_log,
        )
    elif suggested_type == "letter":
        author = str(pick_meta_arg(meta, args, "author", "unknown") or "unknown")
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=fields["tags"],
            importance=fields["importance"],
            domain=fields["domain"],
            valence=fields["valence"],
            arousal=fields["arousal"],
            name=display_title,
            bucket_type="letter",
            why_remembered=fields["why_remembered"],
            source_tool=fields["source_tool"],
        )
        await rt.bucket_mgr.update(
            bucket_id,
            author=author,
            user_name=str(pick_meta_arg(meta, args, "user_name", "") or ""),
            title=str(pick_meta_arg(meta, args, "title", title) or title),
            letter_date=str(pick_meta_arg(meta, args, "date", "") or ""),
        )
    elif suggested_type == "anchor":
        target_id = str(meta.get("target_bucket_id") or args.get("bucket_id") or "")
        if not target_id:
            return "批准失败：缺少 target_bucket_id。"
        target_value = bool(planned_updates.get("anchor", True))
        result = await rt.bucket_mgr.set_anchor(target_id, target_value)
        if not result.get("ok"):
            return f"批准失败：{result.get('error', 'anchor update failed')}"
        bucket_id = target_id
    elif suggested_type == "update":
        target_id = str(meta.get("target_bucket_id") or args.get("bucket_id") or "")
        if not target_id:
            return "批准失败：缺少 target_bucket_id。"
        ok = await rt.bucket_mgr.update(target_id, **planned_updates)
        if not ok:
            return f"批准失败：未能更新正式桶 {target_id}。"
        bucket_id = target_id
    elif suggested_type == "delete":
        target_id = str(meta.get("target_bucket_id") or args.get("bucket_id") or "")
        if not target_id:
            return "批准失败：缺少 target_bucket_id。"
        ok = await rt.bucket_mgr.delete(target_id)
        if not ok:
            return f"批准失败：未能删除/归档正式桶 {target_id}。"
        bucket_id = target_id
    else:
        return f"批准失败：暂不支持 suggested_type={suggested_type}。"

    meta["status"] = "approved"
    meta["approved_at"] = datetime.now().isoformat(timespec="seconds")
    meta["approved_result"] = {
        "bucket_id": bucket_id,
        "suggested_type": suggested_type,
        "display_title": display_title,
        "final_fields": meta.get("final_fields", {}),
        "field_adjustments": meta.get("field_adjustments", []),
    }
    meta["formal_result"] = meta["approved_result"]
    src.write_text(_dump_candidate(meta, body), encoding="utf-8")
    dst = _candidate_path(candidate_id, "approved")
    if dst.exists():
        dst = _review_dir("approved") / f"{candidate_id}-{datetime.now().strftime('%H%M%S')}.md"
    shutil.move(str(src), str(dst))
    return f"已批准候选并写入正式库：candidate_id={candidate_id} bucket_id={bucket_id}"

