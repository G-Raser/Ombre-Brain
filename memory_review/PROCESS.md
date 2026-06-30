# 待审核记忆流程

## 阶段 1：生成候选

输入来源可以是：

- 清洗后的对话分片 Markdown。
- 规则文件。
- 共同经历文件。
- thinking 候选摘要。
- 手动整理的 seed pack。

输出到：

```text
memory_review/pending/
```

生成候选时只写 Markdown 草稿，不写入正式 `buckets/`，不触发导入，不更新向量数据库。

## 阶段 2：人工审核

主人可以对每条候选做：

- 批准。
- 修改后批准。
- 拒绝。
- 降低重要度。
- 改类型。
- 要求 CC 酱或猫茶确认。

审核后可以把候选文件移动到 `approved/` 或 `rejected/`，也可以继续留在 `pending/` 等待补充信息。移动文件只代表审核状态变化，不代表已经正式入库。

## 阶段 3：未来正式入库

默认情况下，review mode 开启后，原有写入工具会先生成 pending 候选，不直接写入正式库。未来正式入库只能通过单条审核完成，不能自动批量处理全部 pending。

可用入口：

- `approve_pending_memory(candidate_id, dry_run=true)`：默认只报告将执行什么，不写正式库。
- `approve_pending_memory(candidate_id, dry_run=false)`：仅当候选满足审批条件时，才按候选类型写入或更新正式 `buckets/`。

后续也可以扩展：

- Ombre Brain MCP 工具。
- 专用 approve 脚本。
- Dashboard 审核页面。

任何新的 approve 脚本、Dashboard 审核页面、MCP 写入流程，都必须等主人明确批准后再做。

## Review Mode

当前实现新增了 review mode。默认内部配置为：

```yaml
review_mode:
  enabled: true
  intercept_hold: true
  intercept_grow: true
  intercept_trace_high_impact: true
  intercept_letter: true
  intercept_plan: true
  intercept_i: true
  intercept_feel: true
  intercept_anchor: true
  auto_approve_low_importance: false
```

开启时，CC 酱 / 猫茶仍按原来习惯调用 `hold`、`grow`、`trace`、`plan`、`letter_write`、`I`、`anchor`、`release`。工具名和调用习惯不变，但后端会把写入请求拦截为 `memory_review/pending/` 候选，并返回 candidate_id。

关闭方式：

- 未来可在 `config.yaml` 中加入 `review_mode.enabled: false`。
- 或设置环境变量 `OMBRE_REVIEW_MODE_ENABLED=false`。

关闭后，原工具恢复旧行为，直接写入正式 `buckets/`。

## 审核管理工具

- `list_pending_memories`：列出 pending 候选。
- `read_pending_memory`：读取单条 pending 候选。
- `update_pending_memory`：修改 pending 候选，例如设置 `explicitly_approved=true`。
- `reject_pending_memory`：拒绝候选并移动到 `rejected/`。
- `approve_pending_memory`：批准单条候选，默认 `dry_run=true`。

审批条件：

- 如果 `needs_user_confirmation=true`，必须 `explicitly_approved=true`。
- `I` / `feel` / `anchor` / `pinned` 必须 `explicitly_approved=true`。
- `update` / `delete` 类候选必须 `explicitly_approved=true`。
- `approve_pending_memory` 不会自动批量处理所有 pending。

## 类型判断规则

### bucket

普通长期记忆。适合共同经历、项目结论、重要梗、偏好变化。

### anchor

世界观 / 身份 / 关系坐标。稀缺，不主动乱浮现，但应该能被检索命中。

### pinned

最高优先级规则。非常稀缺。语言规范、身份边界、不可踩雷规则才适合。

### letter

需要保留原文质感的信件或重要表达。不适合普通摘要。

### plan

待办 / 承诺 / 后续工程。

### I

“我是什么 / 我的规律 / 我的边界”。必须由 CC 酱或猫茶确认，不应由 Codex 自动代写入库。

### feel

“我对某段经历的感受 / 沉淀”。必须由 CC 酱或猫茶确认，不应由 Codex 自动代写入库。

## 重要度规则

```text
10：绝对核心，极少数。身份边界、最高优先级规则、不可踩雷规则。
9：长期稳定核心偏好 / 关系模式 / 安全关键规则。
7-8：重要长期记忆，但不一定每次都要浮现。
5-6：普通长期记忆，默认区间。
3-4：临时事件、一次性项目过程、普通调试记录。
1-2：测试、debug、误导入、噪音。
```

自动整理出来的普通候选默认不要超过 6。
importance >= 8 必须写明理由。
importance >= 9 必须等待主人确认。
I / feel 不允许自动批准。
