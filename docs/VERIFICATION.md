<!--
GENERATED-BY: repository bootstrap, 2026-11-09 +08:00
LAST-EDIT: portable framework synchronization, 2026-11-09
PURPOSE: 记录本地创建版本执行过的验收与未做的部署验证。
ORIGIN: isolated offline tests and direct CLI checks
CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
-->
# 验收记录

## 当前功能迁移验证（2026-11-09）

【实测】在仓库根完整运行 `python -X utf8 -B research.py selftest --plugins`，退出码 0：事务运行时 272 项通过；其余 unittest 共 741 项，其中 730 项通过、11 项因本机为 Windows 而跳过。Python 总计 1,002 项通过、11 项跳过。三个插件共 221 条断言通过，计数单列。

跳过的 11 项属于 [SSH 后端测试](<../tests/test_ssh_backend.py>)的真实 POSIX worker、硬超时与进程组停止。不能据此宣称远端已验收；[SSH 后端](<../kernel/autoresearch/backends/ssh.py>)默认关闭，POSIX 与真实网络仍待验证。适配层禁用、参数和命令构造的本机检查已运行。

[冷启动集成](<../tests/test_cold_start.py>)的 5 项通过：同一个尚不存在的工作区经公开 init、真实 Git/venv 初始化、真实 Monitoring、Accounts/Admission、DSHHost 解析合成日志的独立审计、真实子进程、真实退出码、复盘、自动快照/报告投影和宿主写页回执。修改执行参数或原始依据会拒绝启动。日志与授权均为明确的合成夹具，不当作生产授权。

【实测，独立于合成测试】真实 DSH 子会话的独立派工、父子绑定与报告原文相等已核；真实 scribe 已从空工作区材料写出八节页面，经持久宿主日志回演核字节，得到 `verification=verified`、`session_verified=true`、`delivery_verified=false`。真实当前目标事件亦已只读重放成功；进程内激活状态仍为 unknown。没有公布会话标识、正文或日志。

【未验】真实飞书租户送达及收件、真实 SSH/POSIX 停止、真实云机/正式评测器/平台提交、模型请求实际指令注入、持续无人值守运行和目标的进程内激活状态。上述测试不能证明科学结论成立或用户已看到页面。

下面保留早期版本记录，旧数字不代表当前版本。

## 初始版本记录

本记录对应本地新项目的首次创建。验证只使用新仓库内的合成数据、系统临时目录、模拟宿主和本机回环 HTTP 服务。没有调用真实模型或云机，没有安装宿主插件，也没有注册定时任务。

## 自动验证

仓库根执行：

```powershell
python -X utf8 -B research.py selftest --plugins
python -X utf8 -B tools/check_repository.py --report .local/source-check.json
```

| 检查组 | 结果 |
|---|---|
| 事务运行内核回归 | 272 项 unittest 通过 |
| agent 配置与结果/轨迹适配 | 72 项 unittest 通过 |
| 研究治理 | 90 项 unittest 通过 |
| AutoDL 采集、事件、绑定、worker 与云机护栏 | 46 项 unittest 通过；只用假传输/回环 HTTP |
| 统一入口与完整指令集成 | 5 项 unittest 通过 |
| 指令过滤纯函数 / 模拟宿主 | 17 / 20 条断言通过 |
| 文档遗漏纯函数 / 模拟宿主 | 51 / 24 条断言通过 |
| 事件投递纯函数 / 模拟宿主 | 67 / 42 条断言通过 |
| Python AST、JSON、Markdown 本地链接、已知私有模式 | 静态检查通过；不声称能识别任何可能的私有值 |

Python 合计 **485 项 unittest**；插件合计 **221 条断言**。两种计数口径分别列出，不合成一个误导的总数。

实现与测试只需 Python 标准库。插件测试需要 Node.js。没有要求 pytest，也不会在缺依赖时静默跳过 AutoDL 测试。

## 直接运行的命令

| 入口 | 实测结果 |
|---|---|
| `research.py doctor` | 配置合法，列出三套插件源码；默认真实评测不可运行并给出缺项 |
| `research.py demo --output <新目录>` | 从空状态到 completed；模拟远端只提交 1 次；预算花费 7/100 单位 |
| `research.py init --workspace <新目录>` | 建立空状态与完整指令材料；无旧数据导入 |
| `research.py topics ... create / claim / show` | 专题登记、世代 0→1 接手、只读详情查询通过 |
| `research.py check-plan kernel/templates/plan.template.json` | 结构检查通过；科学判词仍为空 |
| `research.py governance ... freeze / plan / audit-prepare` | 计划冻结、只读取回、审查准备登记通过；只读查询前后数据库字节不变 |
| `research.py profile --tasks ... --output ...` | 默认未配置真实评测，退出 2；没有执行任何命令 |
| `research.py instructions --bundle ... --manifest ...` | 完整正文与逐来源哈希生成成功 |
| `research.py instructions --captured <刚生成的文本>` | 合成往返检查 complete=true；不把它称为真实宿主注入验证 |
| 插件测量/材料核对/加载检查与 patch 生成器 | 正向、负向、未执行路径分开检查，退出码如各工具说明 |
| 计划任务脚本 preview / generate | 能生成供人复核的定义；未注册任务 |

## 指令预算

完整材料超过部分宿主版本的默认总预算。因此部署必须先运行 `instructions` 测量，然后调整总预算和单来源上限，并捕获实际请求核对。这里不写会随文档改动失效的固定配置值。测量建议包含包装和余量，但不能代替真实加载检查。

单来源合并文件也会受到宿主单文件上限影响。不能只调整总预算而不检查这一项。实际部署可能另加用户、目录和技能规则，也必须计入预算。

## 没有验证的事项

- 真实 DSH profile 的装载、模型请求的实际完整注入，以及 doc-gap 的真实模型判定。
- AutoDL 真实接口与开关机、远端环境、正式评测器和真实授权来源。
- 外部消息通道的送达、用户回复真实性，以及生产宿主的独立审查身份验证。
- 持续无人值守运行的自动推进、恢复时间和通知端到端耗时。

这些事项需要部署适配器和现场实测。机械检查通过不证明研究假设成立。

## Git 交付

源码、空配置、契约、模板和合成测试随 Git 提交交付。运行目录、测试夹具、数据库和生成的部署配置均被忽略。发布地址与推送结果由发布后实际核对，不将本地提交称为上传成功。

## 最新框架同步验证

`research.py selftest --plugins` 再次通过：事务运行时 272 项、其余 Python 232 项，共 **504 项 unittest**；三个插件 **221 条断言**。其中新增 19 项升级行为测试，覆盖探索性判据、执行链路指纹、跨版本审计额度、三态未知项理由、历史阻断、显式关闭、小修授权、追加式保护、文件字节绑定与研究页四段。

源运行时 23 件代码及测试与抽取基线的原始 SHA256 一致。新规则在治理层、契约、角色与模板同步。静态语法、JSON、链接及已知私有模式检查通过。完整来源范围和未迁移的生产宿主部分见 [升级记录](UPGRADE_SYNC.md)。

## 本轮增量验证

在 `d764ce3` 之后，本轮完整运行 `research.py selftest --plugins`：事务运行时 272 项，其余 Python 235 项，共 **507 项 unittest**；三个插件仍为 **221 条断言**，全部通过。

新增三项行为测试，覆盖探索性停止条件及来源缺失/空值/类型错误、停止条件变化使旧审计失效且不能作为小修放行、新上下文契约完整进入材料并检测摘要替代。探索性模板经 `check-plan` 校验。静态检查仍只覆盖语法、链接及已知私有模式。

首次 GitHub API 登录检查曾返回 401。随后浏览器登录成功，公开仓库已经创建并完成推送。首次发布的本地与远端 `main` 均为 `d764ce3662230a101b9e76eadc3f999e4c074b1b`。后续同步以实际提交和远端核对为准。
