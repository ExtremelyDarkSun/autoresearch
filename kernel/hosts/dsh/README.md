<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: 2026-11-13（当前框架同步）
PURPOSE: DSH 宿主侧内核目录的索引 —— 五套插件、加载配置生成器、定时任务脚本、三层验收助手各在哪
ORIGIN: none（本文件；原项目把这些散在 `install/` 与 `INSTALL.md` 里）
CUMULATIVE-DELTA: 2026-11-13 接入观察者、补足推进器与当前读取导航。 首版。⛔ 不含任何本机路径或项目名；所有文件都在本目录下，路径相对本文件。
-->

# kernel/hosts/dsh —— DSH 宿主侧内核

> **这里全部是源码与工具，不是「已加载的插件」。** 装法、验收与回滚见 [`docs/dsh.md`](../../../docs/dsh.md)。

## 目录

| 路径 | 内容 |
|---|---|
| [`plugins/agent-instruction-filter/`](<plugins/agent-instruction-filter/>) | 子 agent 指令过滤：`index.js` · `filter-core.mjs` · `policy.mjs`（中性空策略）· 两个自检 · `measure.mjs` · `verify-real-policy.mjs` · `cordis.patch.yml`（`mode: off`）· `docs/`（包元数据说明 · 分类表空模板） |
| [`plugins/dsh-doc-gap/`](<plugins/dsh-doc-gap/>) | 回合末文档遗漏判定：`index.js` · `docgap-core.mjs`（政策外置）· 两个自检 · `measure.mjs` · `verify-real-docs.mjs` · `cordis.patch.yml`（`enabled: false`）· `docs/policy.template.json` |
| [`plugins/autodl-events/`](<plugins/autodl-events/>) | 机器事件投递：`lib/plugin.js`（接线）· `lib/plugin-core.mjs`（纯函数）· 两个转接层 · 两个自检 · `dryrun.mjs` · `README.md` |
| [`plugins/goal-observer/`](<plugins/goal-observer/>) | 原生goal自动轮观察、候选JSON解析重试、失败继续、通知默认关闭、源码打包与模拟宿主自检 |
| [`plugins/goal-observer-ui/`](<plugins/goal-observer-ui/>) | 按goal id/revision/round显示观察消息，原件保留；出厂disabled:true |
| [`tools/gen-profile-patch.mjs`](<tools/gen-profile-patch.mjs>) | 参数化加载配置生成器（只生成，⛔ 不写 profile） |
| [`tools/schtasks.ps1`](<tools/schtasks.ps1>) | Windows 定时任务：预览 / 生成 / 安装 / 卸载 / 查询（默认只预览与生成） |
| [`tools/check-loading.mjs`](<tools/check-loading.mjs>) | 三层验收助手（配置已写 / 宿主已加载 / 目标会话已用） |
| [`config/deployment.example.json`](<config/deployment.example.json>) | 部署参数示例（占位符） |

## 出厂默认（五套都是「关或 hold」）

| 插件 | 出厂配置 | 效果 |
|---|---|---|
| 指令过滤 | `mode: off` + `policy.rules: []` | 不注册监听器；就算打开也逐字不动 |
| 文档遗漏 | `enabled: false` | 不注册监听器 ⇒ 无模型调用 |
| 事件投递 | `fallback: hold` + `maxTargets: 1` + `projects: []` | 没有绑定就一条不发；最多叫醒 1 个 |

| 观察者运行 | `enabled: false` | 不注册监听、不调用模型；通知另需显式启用 |
| 观察者显示 | Loader行 `disabled: true` | 浏览器不加载bundle；`config.enabled:false`不能替代 |

完整操作与验收见[观察者说明](<../../../docs/dsh-goal-observer.md>)。

## 自检（不需要宿主、不联网、不花 token）

```powershell
$node = (Get-Command node).Source   # 用部署机现有的 Node，⛔ 不复制别人的安装布局
& $node plugins\agent-instruction-filter\selftest.mjs
& $node plugins\agent-instruction-filter\selftest-plugin.mjs
& $node plugins\dsh-doc-gap\selftest.mjs
& $node plugins\dsh-doc-gap\selftest-plugin.mjs
& $node plugins\autodl-events\selftest-core.mjs
& $node plugins\autodl-events\selftest-plugin.mjs
```
