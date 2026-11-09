<!--
GENERATED-BY: repository bootstrap and integration acceptance, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09，研究主链、通道、远端与只读接线统一验收
PURPOSE: 记录公开仓库已实测的行为、证据种类和真实部署未验边界。
ORIGIN: isolated offline tests, direct CLI checks and private host probes
CUMULATIVE-DELTA: 用最终集成验收替换早期批次数；保留宿主实测与合成日志的区别。
-->
# 验收记录

## 集成回归

在仓库根运行：

```powershell
python -X utf8 -B research.py selftest --plugins
python -X utf8 -B tools/check_repository.py
```

【实测】最后一次完整回归退出码 0：事务运行时 272 项通过；其余 unittest 共 972 项，其中 961 项通过、11 项跳过。Python 合计 **1,233 项通过、11 项跳过**。三个插件另有 **221 条断言通过**。这次回归已覆盖 WAL 只读连接和远端起跑投影的最终修改。

跳过的 11 项属于 [SSH 后端测试](<../tests/test_ssh_backend.py>)的真实 POSIX worker、硬超时与进程组停止。本机为 Windows；这些用例尚未在 POSIX 机器现场运行。

## 行为证据

| 功能 | 实测范围 |
|---|---|
| 空工作区起步并继续下一轮 | [冷启动集成](<../tests/test_cold_start.py>)6 项：公开 init、真实 Git/venv、真实 Monitoring 与 Accounts/Admission、DSHHost 解析合成独立派工日志、真实子进程及退出码、复盘、自动快照与报告、scribe 字节回执；第二轮共用同一预算账户，未知实际费用的两笔预留均保留，未复盘阻断第三轮 |
| 预算授权顺序 | [准入测试](<../tests/test_admission.py>)核可信用户事件的会话内 seq 与跨会话宿主时间；迟到 renew 不能复活已撤回账户；拒绝新实验授权时无实验或事件残留 |
| 意图撤回与替换 | [监控测试](<../tests/test_monitoring.py>)及[公开命令测试](<../tests/test_monitoring_cli.py>)：自然语言原话须有最新、独立 spawn 报告绑定同一动作和同一条原话；无报告或绑定不符均拒且账本不变；旧 JSON 命令兼容 |
| 材料包不被覆盖 | 冻结墙钟与随机数验证 prepare 的追加序号仍能区分；已存在 id 必须拒绝覆盖 |
| 飞书加密收件 | [加密测试](<../tests/test_feishu_encryption.py>)：官方固定向量及本地生成的 challenge、文字和卡片点击，经过验签、AES-256-CBC 解密、认证、DecisionService 入账；假卡片不会批准；全程假凭据、假传输或回环 HTTP |
| 通道入口 | [通道命令测试](<../tests/test_channels_cli.py>)：配置引用不回显凭据；doctor 不构造通道或建库；listen 缺 cryptography 时启动前拒绝且不写库；收件监听只将已认证事件交给裁决服务 |
| 真只读服务 | [只读测试](<../tests/test_readonly_services.py>)：缺库不建、缺 schema 不迁移、写入口拒绝；实际 check/status/authorize-check 不产生业务写入；WAL 无共享内存时拒绝，避免忽略已提交 WAL 数据；已有共享内存的活动库允许 SQLite 读锁更新锁字节 |
| 受管远端执行 | [远端集成](<../tests/test_remote_workflow.py>)47 项：本地与远端共用起跑复核；claim 和发送前复核授权、来源、审计与监控；失败不发送；共享同笔预算、真实 Runtime/Scheduler 资源租约；unknown 保留预算与资源；起跑立即生成 reporting/AI_WORK 投影，投影失败不阻安全取消 |
| 远端停止判定 | [停止安全测试](<../tests/test_ssh_safety.py>)41 项：假进程与假进程组覆盖晚启动、超时、残留子进程、错 token/request/attempt；两个真实本地进程验证锁排他；没有把这些用例称为真实 POSIX 停止 |
| 实际环境指纹 | [环境测试](<../tests/test_ssh_environment.py>)：真实临时 venv 的安装包元数据改变会改变环境指纹，声明包列表不能替代实际探测 |
| 完整指令 | 全文材料生成、逐来源 SHA256 与范围核对；source 109,236 B、bundle 111,655 B、最大来源15,948 B、建议总上限139,568 B；这组数只是测量，不证明模型实际收到全文 |
| 公开源文件 | Python AST、JSON、Markdown 本地链接与已知私人模式检查；不声称识别所有可能的私有值 |

三个插件位于内核；插件断言计数与 Python unittest 分列。Python 内核使用标准库；加密收件测试另使用可选 `cryptography`，本次环境已安装。插件需要 Node.js 20+，真实 DSH 压缩日志解码需要 Node.js 24+。

## 独立于合成测试的宿主探针

【实测】真实 DSH 子会话的独立派工、父子绑定与报告原文相等已核。真实 scribe 从材料写出八节页面，经持久宿主日志回演核字节，得到 `verification=verified`、`session_verified=true`、`delivery_verified=false`。这不证明用户看到了页面。

【实测】真实目标事件已只读重放；实际受管工具任务与持久日志的绑定已核。任务绑定不证明任务仍活着，也不证明唤醒已送达。目标的进程内自动推进激活状态仍为 unknown。公开仓库不附真实会话标识、日志、页面正文或任务命令。

V1–V3 只有三个合成语义案例的一次独立核。V1 首次报告引用了材料范围之外的未来信息，拒绝后修订通过；不能写成首次通过或机械隔离已证明。另两例分别检查结果夸大与合理探索。样例不等于真实会话中的持续监控已经验收。

## 现场验收与能力边界

以下仍未验证：真实飞书租户加密推送、送达、权限与群 @ 条件；真实 SSH/POSIX 停止；真实云机与正式评测器；平台提交；模型请求的完整指令注入；长期无人值守恢复与唤醒送达。

远端代码与题单上传是部署方的独立步骤，本模块没有自动 scp 上传入口。公开 start 使用生产监控，需要可信宿主原话及独立报告；测试中的假传输不替代这些前置条件。

停止证明来自绑定到本次 request/token/attempt 的 worker 回执，桥接层没有第三方进程观察源。worker 只核本 job 的进程组，不覆盖子进程主动 `setsid` 逃逸；更强保证需要 cgroup/namespace。Windows 无可靠进程组枚举，实际运行后的终态因此保留 unknown；没有启动过的任务可用 never-launched 证明。停止进程组不等于机器空闲或整机关闭。

本地测试通过不证明研究结论成立，不证明真实授权或实际送达。全部109条预期的对应实现及边界见[迁移功能验收](<EXPECTATIONS_ACCEPTANCE.md>)。

## Git 交付

源码、空配置、契约、模板和合成测试随 Git 交付。测试夹具只包含合成内容；运行数据库、部署配置、真实凭据及私有宿主探针均忽略。推送结果以本地提交与远端 main 实际核对为准。
