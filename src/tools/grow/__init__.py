"""
tools/grow/__init__.py - grow 工具入口。

review mode 开启时，grow 只生成 pending candidates，不写正式 buckets。
短内容复用 analyze 打标语义；长内容复用 digest 拆分语义。
"""

from __future__ import annotations

import uuid

from .. import _runtime as rt
from ..review_gate import create_pending_candidate, maybe_compress_batch_importance, review_mode_enabled
from .shortpath import grow_shortpath
from .core import grow_core


async def _review_items_for_grow(stripped: str, batch_id: str) -> list[dict]:
    if len(stripped) < 30:
        analysis = await rt.dehydrator.analyze(stripped)
        raw_importance = analysis.get("importance", 5)
        return [
            {
                "name": analysis.get("suggested_name", ""),
                "content": stripped,
                "domain": analysis.get("domain", ["未分类"]),
                "valence": analysis.get("valence", 0.5),
                "arousal": analysis.get("arousal", 0.3),
                "tags": analysis.get("tags", []),
                "importance": raw_importance,
                "raw_importance": raw_importance,
            }
        ]

    raw_items = await rt.dehydrator.digest(stripped)
    items = []
    for item in raw_items:
        raw_importance = item.get("importance", 5)
        suggested = maybe_compress_batch_importance(
            raw_importance,
            context={"source": "grow_review", "batch_id": batch_id},
        )
        clean_item = dict(item)
        clean_item["raw_importance"] = raw_importance
        clean_item["importance"] = suggested
        if suggested != raw_importance:
            clean_item["importance_adjust_reason"] = "batch_import_compression"
        items.append(clean_item)
    return items


async def dispatch(content: str) -> str:
    await rt.decay_engine.ensure_started()

    if not content or not content.strip():
        return "内容为空，无法整理。"

    stripped = content.strip()
    if review_mode_enabled("intercept_grow"):
        batch_id = f"g_{uuid.uuid4().hex[:12]}"
        try:
            items = await _review_items_for_grow(stripped, batch_id)
        except Exception as e:
            return f"grow review candidate 生成失败，未写入正式 buckets：{e}"

        if not items:
            return "grow review candidate 生成失败：digest/analyze 返回空结果，未写入正式 buckets。"

        candidate_ids = []
        for idx, item in enumerate(items, start=1):
            raw_importance = item.get("raw_importance", item.get("importance", 5))
            candidate = await create_pending_candidate(
                original_tool="grow",
                suggested_type="bucket",
                title=str(item.get("name") or ""),
                content=str(item.get("content") or "").strip(),
                suggested_importance=item.get("importance", 5),
                tags=item.get("tags") or [],
                original_arguments={
                    "content_len": len(str(item.get("content") or "")),
                    "name": item.get("name", ""),
                    "domain": item.get("domain", ["未分类"]),
                    "valence": item.get("valence", 0.5),
                    "arousal": item.get("arousal", 0.3),
                    "tags": item.get("tags") or [],
                    "raw_importance": raw_importance,
                    "importance": item.get("importance", 5),
                    "grow_batch_id": batch_id,
                    "grow_item_index": idx,
                    "importance_adjust_reason": item.get("importance_adjust_reason", ""),
                },
                reason=(
                    "review mode 已开启，grow 已先生成拆分/打标候选并进入 pending；"
                    "主人确认后才可写入正式 buckets。"
                ),
                notes="grow review mode 未调用 merge_or_create，未写入正式 buckets。",
            )
            candidate_ids.append(candidate["candidate_id"])

        return (
            f"候选数量：{len(candidate_ids)}\n"
            f"grow_batch_id：{batch_id}\n"
            "candidate_ids：" + ", ".join(candidate_ids) + "\n"
            "未写入正式记忆库。"
        )

    if len(stripped) < 30:
        return await grow_shortpath(content)
    return await grow_core(content)
