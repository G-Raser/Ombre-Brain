# Cattea Memory New 操作手册（主人版）

> 2026-07-05 大更新：三种重启的区别、备份、维护脚本、改代码流程、故障速查。
> 所有命令默认在项目目录下执行：

```powershell
cd E:\My_CatteaHome\cattea-memory-new
```

## 启动 / 停止

```powershell
.\start-memory-new.ps1      # 懒人启动：up -d + 自动health轮询（推荐）
docker compose up -d        # 手动启动
docker compose down         # 停止并删容器（数据在卷里，不会丢）
```

## 三种"重启"的区别（重要！）

| 改了什么 | 用哪句 |
|---|---|
| 代码（src/ 已同步到 buckets/_app/） | `docker compose restart` |
| buckets/config.yaml | `docker compose restart` |
| **.env**（key、密码等） | `docker compose up -d` （restart不重读env！） |
| requirements.txt / Dockerfile / entrypoint.sh / bump VERSION | `docker compose up -d --build` |

日常加功能就是第一行：改完代码 restart，完事。

## 状态 / 日志 / 健康

```powershell
docker compose ps                                      # 容器状态
docker compose logs --tail 100                         # 最近100行日志
docker compose logs -f                                 # 实时日志（Ctrl+C退出）
(Invoke-WebRequest http://localhost:18021/health).Content   # 健康检查
```

health 正常长这样：`{"status":"ok","buckets":333,"decay_engine":"running"}`

## Dashboard

```text
http://localhost:18021     （密码在 .env 的 OMBRE_DASHBOARD_PASSWORD）
```

审核pending候选、看桶、看日记都在这。

## 备份

```powershell
# 手动备份一次（buckets数据 → C盘，自动留最近14份）
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\backup_buckets.ps1

# 注册每日凌晨4点自动备份（只需运行一次）
schtasks /Create /TN "OmbreBackup" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File E:\My_CatteaHome\cattea-memory-new\tools\backup_buckets.ps1" /SC DAILY /ST 04:00

# 备份在哪 / 日志
dir C:\Users\grase\OmbreBackups\cattea-memory-new\
```

**恢复**（灾难时）：停容器 → 解压某份 `buckets_*.tgz` 覆盖回 `E:\My_CatteaHome\cattea-memory-new\`（里面就是完整的 buckets/ 目录）→ 启动。

## 维护脚本（在容器里跑）

`tools/` 不在镜像里，要先拷进挂载卷（backfill 和 clean_orphan 已拷好）：

```powershell
# 补缺失的 embedding（pulse 报"索引漂移：缺失"时用）
docker exec ombre-brain-cattea-memory-new python /app/buckets/_app/tools/backfill_embeddings.py --dry-run
docker exec ombre-brain-cattea-memory-new python /app/buckets/_app/tools/backfill_embeddings.py

# 清孤儿 embedding（pulse 报"孤儿"时用）
docker exec ombre-brain-cattea-memory-new python /app/buckets/_app/tools/clean_orphan_embeddings.py

# 以后要跑别的 tools/ 脚本：先拷再exec
Copy-Item tools\<脚本>.py buckets\_app\tools\ -Force
docker exec ombre-brain-cattea-memory-new python /app/buckets/_app/tools/<脚本>.py
```

## 改代码流程（自己改或让AI改都一样）

```powershell
# 1. 备份现有运行代码
Copy-Item buckets\_app "backups\app_code_backups\_app_backup_<改动名>_$(Get-Date -Format yyyyMMdd_HHmmss)" -Recurse

# 2. 改 src/ 下的代码（git源头，永远改这里）

# 3. 同步到运行副本
Copy-Item src\<改的文件> buckets\_app\src\<同路径> -Force

# 4. 重启生效
docker compose restart

# 随时校验两边是否一致（应无输出）
# WSL: diff -rq src buckets/_app/src -x __pycache__
```

## Key 更换流程

1. 去 Google AI Studio 生成新 key
2. 改 `.env` 里的 `OMBRE_COMPRESS_API_KEY` 和 `OMBRE_EMBED_API_KEY`（两行，同一把key）
3. `docker compose up -d`（必须重建，restart无效）
4. **key只放.env**，不要写进 config.yaml 或任何文档

## 故障速查

| 症状 | 原因 / 处理 |
|---|---|
| 容器反复崩溃重启 | 看日志。若提示 config 是目录：删掉 `buckets/config.yaml` 目录再启动，entrypoint会自动重建 |
| 改了代码没生效 | 忘了同步 `buckets/_app/src/` 或忘了 restart |
| 改了 .env 没生效 | 用了 restart，应该用 `up -d` |
| pulse 报"索引漂移" | 跑上面的 backfill / clean_orphan |
| embedding 罢工（语义检索失灵） | 日志搜 `standby`：key没配好或额度耗尽 |
| 热补丁代码崩了起不来 | 自带保险：连续启动失败会自动回滚到镜像内置代码；手动回滚=从 `backups/app_code_backups/` 拷回 |
| WSL 侧改不动文件（Permission denied） | 容器写的文件是root属主。用 `sed -i`，或借 `powershell.exe` 从Windows侧改 |

## 重要路径

```text
buckets/                    全部数据（唯一需要备份的东西）
buckets/_app/src/           容器实际运行的代码
src/                        代码源头（git管理，改这里）
legacy_flat_layout/         死代码，别碰
.env                        所有密钥
backups/                    所有备份
memory_review/reports/      改造报告（AI施工记录都在这）
E:\ccChan\docs\虾壳使用手册.md   CC酱视角的详细手册（工具参数全集）
```
