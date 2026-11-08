# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
# PURPOSE: AutoDL 后端包的公开入口（采集 / 事件 / worker / 云机操作适配）
# ORIGIN: none（本文件；原来这些模块是原项目 `tool/` 下的三个平铺脚本）
# CUMULATIVE-DELTA: 首版。只做「导出清单」，⛔ 不含逻辑；子模块可以单独 `import`（互不强制加载）。
"""AutoDL 后端。

模块
----
- `availability` —— 只读采集与判读（`collect` / `render_markdown` / `render_line` / `classify`）
- `events`       —— 变化判定、冷却、首轮基线、持久队列、绑定名单、投递回执
- `worker`       —— 单次 worker（命令行入口；`python -m ...worker --help`）
- `cloud`        —— 云机操作适配（**默认拒绝**；只有 `allow_control=True` 才会发请求）

⚠️ 「读取机器状态」不等于「开机成功」：`availability` 只判字段，`cloud` 只发请求且不回读状态。
"""

from . import availability, cloud, events  # noqa: F401

__all__ = ["availability", "cloud", "events"]
