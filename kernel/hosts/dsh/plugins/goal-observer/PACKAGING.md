<!--
GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
PURPOSE: 记录观察者 Host 插件如何从源码可重建地打包，以及如何校验包内容。
ORIGIN: install/dsh-goal-observer/README.md（部署版只有固定 tgz + 机器特定验收器；这里改成源码重建）
CUMULATIVE-DELTA: 首版。明确三件事：只打 package.json files 列表；manifest.json 无时间戳；没有 npm 时不假装成功。
-->
# 打包（可重建）

本目录**不提交 tgz**。需要安装包时，从源码重建：

```powershell
$node = (Get-Command node).Source
cd kernel\hosts\dsh\plugins\goal-observer
& $node pack.mjs                 # dist/<包名>-<版本>.tgz + dist/manifest.json
& $node pack.mjs --manifest-only # 只生成逐文件清单，不调 npm
& $node pack.mjs --verify        # 用 dist/manifest.json 重新核对源码
```

## 打进去的文件

`pack.mjs` 只读 `package.json` 的 `files` 列表（外加 `package.json` 本身）：

- `index.mjs`（入口）
- `observer-core.mjs`（纯函数核心）
- `cordis.patch.yml`（出厂关闭的补丁模板）
- `README.md` / `package.json.README.md`

⛔ 不打 `node_modules/`、`dist/`、测试文件、`PACKAGING.md`、`pack.mjs`。测试留在仓库里跑。

## 完整性口径

- `dist/manifest.json` 记录每个文件的相对路径、字节数、`sha256`，以及 `integrity`（对 `files` 数组再做一次 sha256）。
- **同一份源码 ⇒ 同一份 manifest.json**（脚本不写时间戳）。
- tgz 的字节可能随 npm/压缩实现变化，**不要把 tgz 的 sha256 当唯一真相**；以 manifest 的逐文件哈希为准。
- 需要复核时跑 `node pack.mjs --verify`；新增/删除/改动任何一个被打包文件都会报出来。

## 边界

- 运行与打包都要求 **Node >= 20.3**（`AbortSignal.any` 从 20.3 才有；20.0/20.1/20.2 不可用）。
- 只用本机 Node 自带的 npm 做本地 `npm pack`，不联网、不发布到 registry。
- 打包成功 ≠ 已安装、≠ 宿主已加载、≠ 目标会话已使用；装载与三层验收见 `docs/dsh.md`。
- 本目录**没有**机器特定验收器（原部署版的 `live-probe*.mjs` / `integration-test-results.json` 依赖本机路径与会话 id，未搬入）。
