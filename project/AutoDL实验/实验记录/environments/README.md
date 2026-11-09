<!--
GENERATED-BY: portable organization, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 +08:00
PURPOSE: 专题环境的落点、填写字段与验收边界。
ORIGIN: existing project placeholder README
CUMULATIVE-DELTA: 恢复中文目录并提供可填写格式，清空实例不清空章节和字段。
-->
# 专题环境

环境由workspace topic按当前Python创建。虚拟环境不跨电脑直接复用，按锁定依赖重建后探测实际解释器和安装包。空目录不表示评测依赖已装。

| 专题 | Python实际版本/路径引用 | 依赖锁文件 | 锁文件SHA256 | 安装命令 | 实际包清单证据 | 一例冒烟 |
|---|---|---|---|---|---|---|
| `<专题>`（模板） | `<新机本地值>` | `<文件>` | `<hash>` | `<命令>` | `<输出路径或未验证>` | `<输入、参数、返回及证据>` |

只填可验证的实际值。requirements声明不是安装成功的证据。CUDA、驱动、模型文件及评分器版本单独登记在环境手册。
