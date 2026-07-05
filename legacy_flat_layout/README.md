# legacy_flat_layout — v2.4.0 平铺布局遗留代码

> 归档日期：2026-07-05。**不要改这里的任何文件。**

这批 `*.py`、`web/` 和 `dashboard.html` 是上游 v2.4.0 时代的仓库根目录平铺布局，
早已不参与任何运行路径（`web/` 是 `src/web/` 的旧快照，缺 journal.py/review.py 等新模块）：

- 容器运行的代码：`buckets/_app/src/`（entrypoint 从镜像播种，支持热补丁）
- git 源头 / 镜像构建源：`src/`（Dockerfile 只 COPY `src/` 和 `frontend/`）
- `tests/` 和 `tools/` 的 `sys.path` 均指向 `src/`
- `render.yaml` 启动命令为 `python src/server.py`
- `zbpack.json` 走 dockerfile 构建

留存原因：来自上游 fork 的 git 追踪文件，保留供对照上游历史。
要改逻辑一律去 `src/` 改，改完按热补丁流程同步到 `buckets/_app/`。
