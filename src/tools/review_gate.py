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


def _cap_importance(suggested_type: str, importance: int, reason: str) -> tuple[int, str]:
    try:
        value = max(1, min(10, int(importance)))
    except (TypeError, ValueError):
        value = 5
    t = suggested_type or "bucket"
    if t == "bucket" and value > 6:
        extra = "普通 hold / grow 候选默认不超过 6；原始重要度已在 pending 阶段降为 6。"
        return 6, f"{reason}\n{extra}".strip()
    if t == "update" and value > 6 and not reason.strip():
        return 6, "普通 update 候选默认不超过 6；原始重要度已在 pending 阶段降为 6。"
    return value, reason


def _metadata_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _first_heading(content: str, fallback: str) -> str:
    m = re.search(r"(?m)^#\s+(.+?)\s*$", content or "")
    return m.group(1).strip() if m else fallback


def _section(content: str, heading: str) -> str:
    pattern = rf"(?ms)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, content or "")
    return match.group(1).strip() if match else ""


def _content_summary(content: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", _section(content, "候选内容") or content or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _candidate_record(path: Path, include_body: bool = False) -> dict:
    meta, content = _load_candidate(path)
    cid = str(meta.get("candidate_id") or path.stem)
    key = path.stem
    title = _first_heading(content, cid)
    record = {
        "candidate_key": key,
        "file_name": path.name,
        "candidate_id": cid,
        "candidate_id_mismatch": cid != key,
        "title": title,
        "suggested_type": meta.get("suggested_type", ""),
        "suggested_importance": meta.get("suggested_importance", ""),
        "tags": meta.get("tags") or [],
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
    tags = tags or []
    original_arguments = original_arguments or {}
    planned_updates = planned_updates or {}
    suggested_importance, reason = _cap_importance(suggested_type, suggested_importance, reason)

    redacted_content, hit_content = _redact_text(content)
    redacted_quote, hit_quote = _redact_text(source_quote)
    redacted_reason, hit_reason = _redact_text(reason)
    redacted_args, hit_args = _redact_text(original_arguments)
    redacted_updates, hit_updates = _redact_text(planned_updates)
    secret_hit = any([hit_content, hit_quote, hit_reason, hit_args, hit_updates])
    if secret_hit:
        notes = (notes + "\n" if notes else "") + "检测到疑似密钥，已脱敏。"

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
    body = f"""# {title.strip() or '候选记忆'}

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
        if target_bucket_id:
            metadata["target_bucket_id"] = target_bucket_id
        if redacted_updates:
            metadata["planned_updates"] = redacted_updates
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
        title = _first_heading(content, cid)
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
    for key, value in updates.items():
        meta[key] = value
    if body:
        content = body
    path.write_text(_dump_candidate(meta, content), encoding="utf-8")
    return f"已更新 pending 候选：{candidate_id}"


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
    suggested_type = str(meta.get("suggested_type") or "bucket")
    title = _first_heading(body, candidate_id)
    content = _section(body, "候选内容")
    tags = meta.get("tags") or []
    importance = int(meta.get("suggested_importance") or 5)
    blockers = _approval_blockers(meta)
    plan = (
        f"候选：{candidate_id}\n"
        f"类型：{suggested_type}\n"
        f"标题：{title}\n"
        f"dry_run：{dry_run}\n"
        f"confirmed：{confirmed}\n"
        f"将执行：按 {suggested_type} 语义写入或更新正式 buckets。"
    )
    if blockers:
        plan += "\n阻止正式批准：\n" + "\n".join(f"- {b}" for b in blockers)
    if dry_run:
        return plan + "\n未修改正式记忆库。"
    if blockers:
        return plan + "\n未修改正式记忆库。"

    bucket_id = ""
    args = meta.get("original_arguments_redacted") or {}
    planned_updates = meta.get("planned_updates") or {}
    if suggested_type == "bucket":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=tags,
            importance=importance,
            domain=["未分类"],
            valence=0.5,
            arousal=0.3,
            name=title,
            source_tool="review_approve",
        )
    elif suggested_type == "pinned":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=tags,
            importance=10,
            domain=["未分类"],
            valence=0.5,
            arousal=0.3,
            name=title,
            bucket_type="permanent",
            pinned=True,
            source_tool="review_approve",
        )
    elif suggested_type == "feel":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=list(dict.fromkeys(["__feel__"] + list(tags))),
            importance=min(importance, 5),
            domain=["feel"],
            valence=0.5,
            arousal=0.3,
            name=title,
            bucket_type="feel",
            triggered_by=str(args.get("source_bucket") or ""),
            source_tool="review_approve",
        )
    elif suggested_type == "I":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=list(dict.fromkeys(["__i__"] + list(tags))),
            importance=min(importance, 6),
            domain=["self"],
            valence=0.5,
            arousal=0.3,
            name=title,
            bucket_type="i",
            weight=0.8,
            source_tool="review_approve",
        )
        await rt.bucket_mgr.update(bucket_id, dont_surface=True)
    elif suggested_type == "plan":
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=["__plan__"],
            importance=7,
            domain=["plan"],
            valence=0.5,
            arousal=0.4,
            name=title,
            bucket_type="plan",
            weight=float(args.get("weight") or 0.5),
            source_tool="review_approve",
        )
        await rt.bucket_mgr.update(
            bucket_id,
            status=str(args.get("status") or "active"),
            related_bucket=str(args.get("related_bucket") or ""),
        )
    elif suggested_type == "letter":
        author = str(args.get("author") or "unknown")
        bucket_id = await rt.bucket_mgr.create(
            content=content,
            tags=["__letter__"],
            importance=10,
            domain=["letter"],
            valence=0.5,
            arousal=0.3,
            name=title,
            bucket_type="letter",
            source_tool="review_approve",
        )
        await rt.bucket_mgr.update(
            bucket_id,
            author=author,
            user_name=str(args.get("user_name") or ""),
            title=str(args.get("title") or title),
            letter_date=str(args.get("date") or ""),
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
    meta["approved_result"] = {"bucket_id": bucket_id, "suggested_type": suggested_type}
    meta["formal_result"] = meta["approved_result"]
    src.write_text(_dump_candidate(meta, body), encoding="utf-8")
    dst = _candidate_path(candidate_id, "approved")
    if dst.exists():
        dst = _review_dir("approved") / f"{candidate_id}-{datetime.now().strftime('%H%M%S')}.md"
    shutil.move(str(src), str(dst))
    return f"已批准候选并写入正式库：candidate_id={candidate_id} bucket_id={bucket_id}"
