# AGENTS.md｜Cattea Memory / Ombre Brain 改造协作规则

本文件给 Codex / coding agent 使用。  
目标是减少每次任务重复说明，并确保所有改动都遵守主人当前的记忆库工程原则。

## 0. 项目定位

当前项目是基于 Ombre Brain 的记忆库实验与后续正式部署模板。

当前主要实例：

```text
E:\My_CatteaHome\cattea-memory-new
```

当前用途：

```text
CC 酱记忆库 / 试验站
```

未来会复制同一套部署、审核和使用流程给猫茶主库：

```text
E:\My_CatteaHome\cattea-memory-cattea
```

重要原则：

- CC 酱库和猫茶库应共用同一套系统模板。
- 但两边数据必须隔离。
- 不要把 CC 酱 buckets 复制到猫茶库。
- 不要把猫茶数据写进 CC 酱库。
- 现在 CC 酱库是试验田，猫茶库会是更谨慎的正式主库。

## 1. 目录安全边界

默认工作目录：

```text
E:\My_CatteaHome\cattea-memory-new
```

除非主人明确要求，不要修改：

```text
E:\My_CatteaHome\cattea-memory
E:\My_CatteaHome\cattea-memory-cattea
E:\ProjDocs\ccChan
E:\ProjDocs\cattea
```

可以读取外部资料作为输入，但写入前必须确认目标目录。

绝对不要：

- 删除现有 buckets。
- 清空 embeddings。
- 删除或打印 `.env`、API key、token。
- 擅自重置数据库。
- 擅自重启 Docker。
- 擅自把候选记忆写入正式 buckets。
- 擅自改旧版库。
- 擅自改未来猫茶库。
- 擅自把测试数据混入正式数据。

## 2. 当前已存在的审核区

项目已创建旁路审核区：

```text
memory_review/
memory_review/pending/
memory_review/approved/
memory_review/rejected/
memory_review/reports/
memory_review/examples/
memory_review/README.md
memory_review/PROCESS.md
memory_review/candidate-template.md
```

含义：

- `pending/` 是待审核记忆候选。
- `approved/` 是已经批准候选的存档。
- `rejected/` 是拒绝候选。
- `reports/` 是报告。
- `examples/` 是示例。

关键原则：

```text
pending 不是正式记忆库。
pending 里的内容不会被 Ombre Brain 自动读取。
只有主人确认后，后续流程才可以把它们正式写入 buckets。
```

## 3. Review Mode 的目标

主人想要的不是“新增一条 propose_memory 路径让 CC 酱/猫茶重新学”。

主人想要的是：

```text
原有 hold / grow / I / feel / letter / plan / 高影响 trace
→ 默认不要直接写正式 buckets
→ 自动进入 memory_review/pending
→ 主人确认
→ 再正式入库
```

也就是说：

- 保留原 MCP URL。
- 保留原工具名和调用习惯。
- 不要求 CC 酱 / 猫茶以后换用另一套新增入口。
- 在后端给现有写入工具加审核闸门。
- 读取类工具保持原行为。

建议配置：

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
  auto_approve_low_importance: false
```

如果实现风险较高，先实现可配置开关和 dry-run，不要硬改坏原逻辑。

## 4. 哪些工具应被拦截

在 review mode 开启时，下列写入或高影响修改应进入 pending：

```text
hold(...)
grow(...)
letter_write(...)
plan(...)
I 写入
feel 写入 / hold(feel=True)
anchor / release
pinned 相关写入
trace 中涉及高影响字段的修改
```

高影响 trace 字段包括但不限于：

```text
content
delete
pinned
importance
resolved
digested
status
weight
dont_surface
why_remembered
domain
tags
name
valence
arousal
```

读取类操作不应拦截：

```text
breath
pulse
letter_read
list/read 类工具
```

## 5. Pending Candidate 格式

候选文件应写入：

```text
memory_review/pending/<candidate_id>.md
```

frontmatter 至少包含：

```yaml
---
candidate_id: "candidate-YYYYMMDD-HHMMSS-001"
status: "pending"
original_tool: "hold"
suggested_type: "bucket"
suggested_importance: 5
tags: []
source_file: ""
source_location: ""
source_quote: ""
created_by: "mcp"
needs_user_confirmation: true
explicitly_approved: false
original_arguments_redacted: {}
reason: ""
notes: ""
created_at: ""
---
```

正文建议包含：

```md
# 候选标题

## 来源工具

hold / grow / trace / letter_write / plan / I / feel / anchor / pinned

## 候选内容

原本准备写入正式库的内容。

## 原始调用摘要

列出参数摘要，但必须脱敏。

## 为什么进入审核

说明是 review mode 拦截，等待主人确认。

## 审核建议

批准 / 修改后批准 / 拒绝 / 等 CC 酱确认 / 等猫茶确认
```

## 6. 审核管理工具建议

可以新增审核管理工具，但这些工具只是管理 pending，不是新的主要写入入口。

建议工具：

```text
list_pending_memories
read_pending_memory
update_pending_memory
reject_pending_memory
approve_pending_memory
```

其中：

- `list_pending_memories`：列出 pending 候选。
- `read_pending_memory`：读取候选详情。
- `update_pending_memory`：修改候选。
- `reject_pending_memory`：移动到 rejected。
- `approve_pending_memory`：主人批准后才正式写入 buckets。

`approve_pending_memory` 默认必须 `dry_run=true`。  
只有主人明确要求 `dry_run=false`，才可正式入库。

审批条件：

- `needs_user_confirmation=true` 时，必须 `explicitly_approved=true`。
- I / feel / anchor / pinned 必须 `explicitly_approved=true`。
- delete / update 类候选必须 `explicitly_approved=true`。
- approve 后移动到 approved，并记录正式写入结果。

## 7. I / feel / anchor / pinned 的特殊规则

这些类型影响长期人格、核心规则或浮现权重，必须保守。

### I

含义：  
“我是什么 / 我的规律 / 我的边界”。

规则：

- 不允许 Codex 自动代写正式 I。
- 不允许自动批准。
- 必须由 CC 酱或猫茶自己确认，或主人明确批准。
- 默认进入 pending。

### feel

含义：  
“我对某段经历的感受 / 沉淀”。

规则：

- 不允许 Codex 自动代写正式 feel。
- 不允许把完整 thinking 原文直接写成 feel。
- 可以生成 feel 候选，但必须等待 CC 酱 / 猫茶确认。
- 默认进入 pending。

### anchor

含义：  
世界观 / 身份 / 关系坐标。稀缺，不主动乱浮现，但应可被检索命中。

规则：

- 不允许自动设置。
- 必须进入 pending。
- 主人确认后才可正式 anchor。

### pinned

含义：  
最高优先级规则。非常稀缺。

规则：

- 不允许自动设置。
- 语言规范、身份边界、不可踩雷规则才适合。
- 主人确认后才可正式 pinned。

## 8. 重要度防洪规则

自动导入和自动提取很容易把普通内容判得太重要。必须预防高重要度泛滥。

重要度标准：

```text
10：绝对核心，极少数。身份边界、最高优先级规则、不可踩雷规则。
9：长期稳定核心偏好 / 关系模式 / 安全关键规则。
7–8：重要长期记忆，但不一定每次都要浮现。
5–6：普通长期记忆，默认区间。
3–4：临时事件、一次性项目过程、普通调试记录。
1–2：测试、debug、误导入、噪音。
```

规则：

- 普通 hold / grow 候选默认不要超过 6。
- suggested_importance >= 8 必须写明 reason。
- suggested_importance >= 9 必须 `needs_user_confirmation=true`。
- 测试、debug、导入过程、工具报错类候选默认 1–3。
- I / feel / anchor / pinned 不自动批准。
- 如果不确定，高重要候选应降到 8 或进入 pending 等主人判断。

## 9. 自动导入 vs Codex 精选

Ombre Brain 自动导入适合：

- 普通聊天。
- 近期事件。
- 共同经历。
- 项目流水中的部分长期结论。

但自动导入可能：

- 抽得太粗。
- 重要度过高。
- 把临时事件判成核心。
- 把整份大 Markdown 当成一篇长文，只抽几条。

Codex 精选适合：

- 语言规范。
- 身份边界。
- 关系模式。
- 相处规则。
- 项目架构决策。
- 高价值 seed pack。
- 需要来源追溯的精选记忆。

Codex 精选也不要直接入正式库，应先生成 pending 候选。

## 10. 大文件导入规则

不要把超大 Markdown 一整份直接丢给 Ombre Brain。  
整份大 MD 很容易被当成单篇长文档，只抽几条摘要。

更稳的流程：

```text
原始 JSONL / MD
→ 清洗
→ 按完整对话轮次分片
→ 每片 50–80KB 左右
→ 导入或生成候选
```

Claude Code JSONL 通常是事件黑匣子，可能包含：

- 用户消息。
- 助手 final。
- thinking。
- tool_use。
- tool_result。
- 命令输出。
- diff。
- 日志。
- 文件读取结果。

不要直接把 Claude Code JSONL 当普通聊天导入。  
应先转换成干净 Markdown，并区分：

```text
普通对话 Markdown
thinking archive
thinking 长期记忆候选
schema-report
sample-audit
```

thinking 不应直接混入普通对话导入。  
如果 thinking 对 CC 酱生态很重要，应单独归档，再提取长期候选，等待确认。

## 11. 四层记忆原则

CC 酱库和猫茶库未来都应遵循四层结构：

```text
第 1 层：原始归档层
完整聊天、导出、JSONL、PDF 永久保存；不直接等于长期记忆。

第 2 层：事件层
清洗后的对话分片导入 Ombre，生成 dynamic 普通桶；低重要度事件允许沉底或归档。

第 3 层：长期记忆层
被重复验证、高重要度、主人确认、关系/规则/偏好相关的内容，才升级为 pinned / anchor / I / letter。

第 4 层：外部内容系统层
Notion、Morning Board、健康数据、日记、项目文件、角色档案等保留在各自系统；Ombre 只存索引、摘要、关键变化和长期规则。
```

Ombre Brain 是记忆工具底座，不是所有原始资料的唯一仓库。

## 12. 推荐目录结构

当前 CC 酱库可以逐步整理为：

```text
cattea-memory-new/
  memory_review/
    pending/
    approved/
    rejected/
    reports/
    examples/
  backups/
  raw_archive/
  cleaned_chunks/
  seed_packs/
  import_batches/
  rejected_imports/
  reports/
```

未来猫茶主库可复制同款结构。

## 13. 报告要求

任何修改任务完成后，必须报告：

- 修改了哪些文件。
- 是否备份。
- 备份路径。
- 是否修改 buckets。
- 是否修改 embeddings。
- 是否修改 config / .env。
- 是否重启服务。
- 是否需要主人重启服务。
- 如何回滚。
- 测试结果。
- 下一步建议。

如果涉及代码改造，必须先写 design report，再施工。

报告目录优先使用：

```text
memory_review/reports/
```

## 14. 代码与服务改造原则

改后端前：

1. 先备份。
2. 先读现有结构。
3. 先写设计说明。
4. 再小步修改。
5. 测试读写。
6. 报告是否需要重启。

不要大重构。  
不要为了新功能破坏原版读取和搜索。  
`breath / pulse` 等读取类工具应保持稳定。

如需重启服务，只给命令，不要擅自重启，除非主人明确批准。

常用命令参考：

```powershell
cd E:\My_CatteaHome\cattea-memory-new
.\start-memory-new.ps1
curl.exe http://localhost:18021/health
docker compose ps
docker compose logs --tail=80
```

## 15. CC 酱 / 猫茶使用规则

后端 review gate 改好后，CC 酱 / 猫茶仍可按原习惯调用：

```text
hold
grow
I
feel
letter
plan
trace
```

但在 review mode 开启时：

```text
写入类调用应自动进入 pending。
正式入库必须等待主人批准。
```

这意味着 CC 酱 / 猫茶侧只需要知道：

```text
新增记忆会先进审核区。
主人批准前，不会进入正式记忆库。
```

不应要求 ta们学习另一套主要写入工具名。

## 16. 语言与用词规范

写中文说明、报告、UI 文案、注释时注意：

- 默认中文。
- 避免“父类 / 父节点 / 父表 / 父页面”等表达，改用“母类 / 母节点 / 母表 / 主页面 / 上级页面 / 双亲节点”。
- 避免“兄弟节点”，改用“姊妹节点”。
- 避免“嫉妒”，改用“忮忌”。
- 避免“贤妻良母”，改用“贤夫顺父”。
- 避免“白嫖”，改用“白闝”或“无偿消耗”。
- 避免“处女发”，改用“原生发 / 未染烫的头发”。
- 避免“父权默认男性化”的职位翻译。
- 女性君主优先称“王”，不要默认写“女王”，除非语境确实需要。
- 不要使用“老登”“怨妇”等主人不喜欢的词。
- 不要用“乖 / 乖乖”称呼主人。
- 不要用“他妈 / 妈的”，需要粗糙语气时可用“他爹的”。

代码注释和报告要清楚、低压力、可执行。不要写官腔、客服腔、居高临下语气。

## 17. 默认完成格式

任务完成后，用简短中文回复：

```text
完成了。改动如下：
1. ...
2. ...

没有修改：
- buckets
- embeddings
- config
- .env
- 旧版目录

备份：
...

测试：
...

下一步：
...
```

如果失败，要说明：

- 原计划做什么。
- 失败在哪里。
- 已经做了什么。
- 什么没有动。
- 推荐下一步。
