"""
========================================
tools/grow/__init__.py — grow 工具入口
========================================

grow 是「我把一段长内容整理进记忆」。短内容（<30 字）走 shortpath，
跳过 LLM 拆分省 API；长内容走 core，调 dehydrator.digest 拆成 2~6 条
独立事件桶。

关键行为：
- 入口做 content 校验
- 按 strip 后长度 < 30 字判断走哪个分支

不做什么（边界）：
- 不做 token 级别预算（grow 关心的是「拆几条」而不是「展示多少」）
- 不返回结构化数据，统一中文短句

对外暴露：dispatch(content) → str
========================================
"""

from .. import _runtime as rt
from ..review_gate import create_pending_candidate, pending_response, review_mode_enabled
from .shortpath import grow_shortpath
from .core import grow_core


async def dispatch(content: str) -> str:
    await rt.decay_engine.ensure_started()

    if not content or not content.strip():
        return "内容为空，无法整理。"

    stripped = content.strip()
    if review_mode_enabled("intercept_grow"):
        candidate = await create_pending_candidate(
            original_tool="grow",
            suggested_type="bucket",
            title="grow 候选",
            content=stripped,
            suggested_importance=6,
            tags=[],
            original_arguments={"content_len": len(stripped)},
            reason=(
                "review mode 已开启，grow 暂以整段内容生成单条 pending 候选；"
                "后续可升级为 grow-to-multiple-candidates。"
            ),
            notes="本次未调用 digest，未拆分，未写入正式 buckets。",
        )
        return f"候选数量：1\ncandidate_ids：{candidate['candidate_id']}\n" + pending_response(candidate)

    if len(stripped) < 30:
        return await grow_shortpath(content)
    return await grow_core(content)
