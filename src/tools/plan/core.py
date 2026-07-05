"""
========================================
tools/plan/core.py — plan / letter_write / letter_read 实现
========================================

plan 桶记录我答应过、答应自己或想完成的事；letter 桶是她/他与 OB
之间的长信件。它们都是独立类型，永久保存、不衰减、不出现在普通
breath 中。

关键行为：
- plan_create：去重（同正文 + status=active 已存在 → 直接返回原 ID），
  写入 type=plan + status + weight + change_log 起点
- letter_write：原文永久保存，author 接受任意字符串署名（"ai" 或等于
  ai_name 时统一存为 ai_name 的值，其它字符串原样存为署名；"user" 为
  用户侧），写入 type=letter + author/title/letter_date 元数据
- letter_read：默认按时间倒序；带 query 时走向量近邻；支持 author /
  date_from / date_to 过滤；author 字段原样返回存储的署名，不做转换

不做什么（边界）：
- plan 不做向量去重，只做精确文本去重
- letter 永不合并、永不压缩、永不被衰减归档

对外暴露：plan_create / letter_write / letter_read
========================================
"""

import json
from typing import Optional

from .. import _runtime as rt
from ..review_gate import create_pending_candidate, pending_response, review_mode_enabled
from .status import normalize_plan_status_for_formal
from utils import strip_wikilinks, get_ai_name


async def plan_create(
    content: str,
    status: Optional[str] = "active",
    related_bucket: Optional[str] = "",
    weight: Optional[float] = 0.5,
    why_remembered: Optional[str] = "",
) -> str:
    if status is None: status = "active"
    if related_bucket is None: related_bucket = ""
    if weight is None: weight = 0.5
    if why_remembered is None: why_remembered = ""
    weight = max(0.0, min(1.0, float(weight)))
    why_remembered = str(why_remembered).strip()[:500]
    await rt.decay_engine.ensure_started()
    if not content or not content.strip():
        return "内容为空，无法登记计划。"
    status = normalize_plan_status_for_formal(status)

    if review_mode_enabled("intercept_plan"):
        candidate = await create_pending_candidate(
            original_tool="plan",
            suggested_type="plan",
            title="plan 候选",
            content=content.strip(),
            suggested_importance=7,
            tags=["__plan__"],
            original_arguments={
                "content_len": len(content or ""),
                "status": status,
                "related_bucket": related_bucket,
                "weight": weight,
                "why_len": len(why_remembered or ""),
            },
            reason="review mode 已开启，plan 写入默认进入 pending，主人确认后才可进入正式 buckets。",
            notes="plan 是待办/承诺类候选，本次未写入正式计划桶。",
        )
        return pending_response(candidate)

    norm = content.strip()
    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
        for b in all_buckets:
            m = b.get("metadata", {})
            if (
                m.get("type") == "plan"
                and m.get("status", "active") == "active"
                and (b.get("content") or "").strip() == norm
            ):
                return f"跟原有 active plan 完全重复→{b['id']}（未重复登记）"
    except Exception as e:
        rt.logger.warning(f"plan() dedup scan failed: {e}")

    bucket_id = await rt.bucket_mgr.create(
        content=content.strip(),
        tags=["__plan__"],
        importance=7,
        domain=["plan"],
        valence=0.5,
        arousal=0.4,
        name=None,
        bucket_type="plan",
        why_remembered=why_remembered,
        weight=weight,
        source_tool="plan",
    )
    from .._common import append_plan_change_log
    initial_log = append_plan_change_log([], "created", to=status)
    update_kwargs = {"status": status, "change_log": initial_log}
    if related_bucket.strip():
        update_kwargs["related_bucket"] = related_bucket.strip()
    try:
        await rt.bucket_mgr.update(bucket_id, **update_kwargs)
    except Exception as e:
        rt.logger.warning(f"plan() failed to set status/related: {e}")
    try:
        await rt.embedding_engine.generate_and_store(bucket_id, content)
    except Exception:
        pass
    return f"📋plan→{bucket_id} [{status}]"


async def plan_read(
    query: Optional[str] = "",
    status: Optional[str] = "active",
    tags: Optional[str] = "",
    domain: Optional[str] = "",
    date_from: Optional[str] = "",
    date_to: Optional[str] = "",
    max_results: Optional[int] = 20,
    include_content: Optional[bool] = False,
    content_max_chars: Optional[int] = 800,
) -> str:
    if query is None: query = ""
    if status is None: status = "active"
    if tags is None: tags = ""
    if domain is None: domain = ""
    if date_from is None: date_from = ""
    if date_to is None: date_to = ""
    if max_results is None: max_results = 20
    if include_content is None: include_content = False
    if content_max_chars is None: content_max_chars = 800

    status_norm = str(status).strip().lower() or "active"
    if status_norm not in ("active", "resolved", "abandoned", "all"):
        status_norm = "active"
    max_results = max(1, min(100, int(max_results)))
    content_max_chars = max(1, min(8000, int(content_max_chars)))

    query_text = str(query).strip().lower()
    required_tags = [t.strip() for t in str(tags).split(",") if t.strip()]
    domain_filter = str(domain).strip()

    start = str(date_from).strip()
    end = str(date_to).strip()
    if len(start) == 10:
        start = f"{start}T00:00:00"
    if len(end) == 10:
        end = f"{end}T23:59:59"

    try:
        all_buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"plan_read failed: {e}"}, ensure_ascii=False)

    def _status(meta: dict) -> str:
        raw = str(meta.get("status") or "active").strip().lower()
        if raw in ("resolved", "abandoned"):
            return raw
        return "active"

    def _created(meta: dict) -> str:
        return str(meta.get("created") or meta.get("created_at") or "")

    def _updated(meta: dict) -> str:
        return str(
            meta.get("updated")
            or meta.get("updated_at")
            or meta.get("last_active")
            or meta.get("created")
            or meta.get("created_at")
            or ""
        )

    def _domains(meta: dict) -> list[str]:
        value = meta.get("domain") or []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    def _tags(meta: dict) -> list[str]:
        value = meta.get("tags") or []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(v) for v in value]
        return []

    def _matches_query(bucket: dict, meta: dict, tag_list: list[str]) -> bool:
        if not query_text:
            return True
        parts = [
            str(bucket.get("content") or ""),
            str(meta.get("name") or ""),
            str(meta.get("title") or ""),
            str(meta.get("related_bucket") or ""),
            str(meta.get("why_remembered") or ""),
            " ".join(tag_list),
        ]
        return query_text in "\n".join(parts).lower()

    def _preview(content: str) -> str:
        text = " ".join(strip_wikilinks(content).split())
        if len(text) <= 240:
            return text
        return text[:240].rstrip() + "..."

    plans = []
    for bucket in all_buckets:
        meta = bucket.get("metadata", {})
        if meta.get("type") != "plan":
            continue

        st = _status(meta)
        if status_norm != "all" and st != status_norm:
            continue
        if status_norm == "all" and st not in ("active", "resolved", "abandoned"):
            continue

        tag_list = _tags(meta)
        tag_set = set(tag_list)
        if required_tags and not all(t in tag_set for t in required_tags):
            continue

        domain_list = _domains(meta)
        if domain_filter and domain_filter not in domain_list:
            continue

        created = _created(meta)
        if start and created and created < start:
            continue
        if end and created and created > end:
            continue

        if not _matches_query(bucket, meta, tag_list):
            continue

        content = str(bucket.get("content") or "")
        item = {
            "bucket_id": bucket.get("id") or meta.get("id") or "",
            "title": meta.get("title") or meta.get("name") or "",
            "status": st,
            "created": created,
            "updated": _updated(meta),
            "weight": meta.get("weight", 0.5),
            "related_bucket": meta.get("related_bucket") or "",
            "tags": tag_list,
            "content_preview": _preview(content),
            "content_length": len(content),
        }
        if include_content:
            item["content"] = strip_wikilinks(content)[:content_max_chars]
        plans.append(item)

    plans.sort(key=lambda p: p.get("updated") or p.get("created") or "", reverse=True)
    plans = plans[:max_results]
    return json.dumps(
        {
            "ok": True,
            "query": str(query).strip(),
            "status": status_norm,
            "count": len(plans),
            "max_results": max_results,
            "include_content": bool(include_content),
            "content_max_chars": content_max_chars if include_content else 0,
            "results": plans,
        },
        ensure_ascii=False,
        indent=2,
    )


async def letter_write(
    author: str,
    content: str,
    user_name: Optional[str] = "",
    title: Optional[str] = "",
    date: Optional[str] = "",
    ai_name: Optional[str] = "",
) -> str:
    if user_name is None: user_name = ""
    if title is None: title = ""
    if date is None: date = ""
    # ai_name：显式传入优先，否则取环境变量 AI_NAME（回退 "AI"）。
    ai = (ai_name or "").strip() or get_ai_name()
    if not author or not author.strip():
        return "author 不能为空。"
    if not content or not content.strip():
        return "信件内容不能为空。"

    # 署名归一化：
    #   - "user" → 用户侧，存 "user"（用户名另存 user_name，逻辑不变）
    #   - "ai" / 等于 ai_name / 旧值 "claude"（历史兼容）→ 统一存 ai_name 的值
    #   - 其它任意字符串 → 原样作为署名
    raw = author.strip()
    low = raw.lower()
    if low == "user":
        a = "user"
    elif low in ("ai", "claude") or raw == ai:
        a = ai
    else:
        a = raw

    if review_mode_enabled("intercept_letter"):
        candidate = await create_pending_candidate(
            original_tool="letter_write",
            suggested_type="letter",
            title=(title.strip()[:60] or f"{a}_{date.strip() or 'letter'}"),
            content=content.strip(),
            suggested_importance=10,
            tags=["__letter__"],
            original_arguments={
                "author": a,
                "content_len": len(content or ""),
                "user_name": user_name,
                "title": title,
                "date": date,
                "ai_name": ai,
            },
            reason="review mode 已开启，letter_write 默认进入 pending，主人确认后才可进入正式 letters。",
            notes="letter 需保留原文质感，高重要度候选必须 explicitly_approved=true。",
        )
        return pending_response(candidate)

    extra_meta = {"author": a}
    if user_name.strip():
        extra_meta["user_name"] = user_name.strip()
    if title.strip():
        extra_meta["title"] = title.strip()[:120]
    if date.strip():
        extra_meta["letter_date"] = date.strip()

    bucket_id = await rt.bucket_mgr.create(
        content=content.strip(),
        tags=["__letter__"],
        importance=10,
        domain=["letter"],
        valence=0.5,
        arousal=0.3,
        name=(title.strip()[:60] or f"{a}_{date.strip() or 'letter'}"),
        bucket_type="letter",
        source_tool="letter",
    )
    try:
        await rt.bucket_mgr.update(bucket_id, **extra_meta)
    except Exception as e:
        rt.logger.warning(f"letter_write update meta failed: {e}")
    try:
        await rt.embedding_engine.generate_and_store(bucket_id, content)
    except Exception:
        pass
    return f"💌letter→{bucket_id} [{a}]"


async def letter_read(
    query: Optional[str] = "",
    limit: Optional[int] = 10,
    author: Optional[str] = "",
    date_from: Optional[str] = "",
    date_to: Optional[str] = "",
) -> str:
    if query is None: query = ""
    if limit is None: limit = 10
    if author is None: author = ""
    if date_from is None: date_from = ""
    if date_to is None: date_to = ""
    limit = max(1, min(50, limit))
    try:
        all_b = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as e:
        return f"读取信件失败: {e}"
    letters = [b for b in all_b if b["metadata"].get("type") == "letter"]
    af = author.strip()
    if af:
        ai = get_ai_name()
        af_low = af.lower()
        if af_low == "user":
            letters = [b for b in letters if b["metadata"].get("author") == "user"]
        elif af_low in ("ai", "claude") or af == ai:
            # AI 侧：匹配新署名 ai_name + 历史遗留的 "claude"
            ai_aliases = {ai, "claude"}
            letters = [b for b in letters if b["metadata"].get("author") in ai_aliases]
        else:
            # 任意自定义署名：精确匹配存储值
            letters = [b for b in letters if b["metadata"].get("author") == af]

    def _within(b):
        d = b["metadata"].get("letter_date") or b["metadata"].get("created", "")
        if date_from and d and d < date_from: return False
        if date_to and d and d > date_to: return False
        return True

    letters = [b for b in letters if _within(b)]

    query_text = query.strip()

    def _matches_query(b):
        if not query_text:
            return True
        meta = b.get("metadata", {})
        parts = [
            b.get("content", ""),
            str(meta.get("name") or ""),
            str(meta.get("title") or ""),
            str(meta.get("author") or ""),
        ]
        parts.extend(str(t) for t in (meta.get("tags") or []))
        return query_text.lower() in "\n".join(parts).lower()

    if query_text and rt.embedding_engine and getattr(rt.embedding_engine, "enabled", False):
        try:
            sims = await rt.embedding_engine.search_similar(query_text, top_k=limit * 3)
            id_score = {bid: sc for bid, sc in sims}
            vector_matches = [b for b in letters if b["id"] in id_score]
            if vector_matches:
                letters = vector_matches
                letters.sort(key=lambda b: id_score.get(b["id"], 0.0), reverse=True)
            else:
                letters = [b for b in letters if _matches_query(b)]
                letters.sort(key=lambda b: b["metadata"].get("letter_date") or b["metadata"].get("created", ""), reverse=True)
        except Exception as e:
            rt.logger.warning(f"letter_read vector search failed: {e}")
            letters = [b for b in letters if _matches_query(b)]
            letters.sort(key=lambda b: b["metadata"].get("created", ""), reverse=True)
    else:
        if query_text:
            letters = [b for b in letters if _matches_query(b)]
        letters.sort(key=lambda b: b["metadata"].get("letter_date") or b["metadata"].get("created", ""), reverse=True)

    letters = letters[:limit]
    if not letters:
        return "没有找到匹配的信件。"
    parts = []
    for b in letters:
        m = b["metadata"]
        a = m.get("author", "?")
        d = (m.get("letter_date") or m.get("created", ""))[:10]
        title = m.get("title") or m.get("name", "")
        parts.append(
            f"[{b['id']}] {a} · {d}{(' · ' + title) if title else ''}\n"
            + strip_wikilinks(b["content"])
        )
    return "=== 信件 ===\n" + "\n\n---\n\n".join(parts)
