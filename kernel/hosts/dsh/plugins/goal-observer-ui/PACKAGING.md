<!--
GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
PURPOSE: 记录观察者 UI 插件如何从源码可重建地打包，以及如何校验包内容。
ORIGIN: install/dsh-goal-observer-ui/package.README.md（部署版只有说明，没有打包脚本）
CUMULATIVE-DELTA: 首版。只打 package.json files 列表；manifest.json 无时间戳；没有 npm 时不假装成功。
-->
# 打包（可重建）

本目录**不提交 tgz**。需要安装包时，从源码重建：

```powershell
$node = (Get-Command node).Source
cd kernel\hosts\dsh\plugins\goal-observer-ui
& $node pack.mjs                 # dist/<包名>-<版本>.tgz + dist/manifest.json
& $node pack.mjs --manifest-only # 只生成逐文件清单，不调 npm
& $node pack.mjs --verify        # 用 dist/manifest.json 重新核对源码
```

## 打进去的文件

`pack.mjs` 只读 `package.json` 的 `files` 列表（外加 `package.json` 本身）：

- `index.js`（宿主侧空入口，让宿主发现包）
- `client.js`（浏览器端显示插件）
- `cordis.patch.yml`（只插一行）
- `README.md` / `package.json.README.md`

⛔ 不打 `node_modules/`、`dist/`、测试文件、`PACKAGING.md`、`pack.mjs`。测试留在仓库里跑。

## 完整性口径

- `dist/manifest.json` 记录每个文件的相对路径、字节数、`sha256`，以及 `integrity`。
- 同一份源码 ⇒ 同一份 manifest.json（脚本不写时间戳）。
- tgz 字节可能随 npm/压缩实现变化；以 manifest 的逐文件哈希为准。
- 复核跑 `node pack.mjs --verify`。

## 边界

- 只用本机 Node 自带的 npm 做本地 `npm pack`，不联网、不发布到 registry。
- 打包成功 ≠ 宿主已加载、≠ 浏览器已刷新；显示验收见 README「未验证边界」。
