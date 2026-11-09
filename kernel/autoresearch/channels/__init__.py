# GENERATED-BY: feishu 通道适配层导出, 2026-11-10 +08:00
# LAST-EDIT: 2026-11-10 +08:00
# PURPOSE: 部署通道适配层的唯一入口 —— 目前只有飞书，默认禁用，凭据只留引用
# ORIGIN: kernel/autoresearch/decisions.py 的 ApprovalChannel 协议（通用决策链只带 FakeChannel）
# CUMULATIVE-DELTA: 首版。导出 FeishuChannel / FeishuConfig / from_mapping / make_http_handler。
"""部署通道适配层。

本包里的模块是**真实传输**实现，与 `tests/test_decisions.py` 里的 `FakeChannel` 同一份接口，
因此通用决策链（页 → 卡 → 回执 → 入向 → 裁决）可以在**离线**下跑真实适配层代码：
把 `transport` 与 `credential_source` 注入进去即可，不需要网络、不需要凭据。

```python
from kernel.autoresearch.channels import FeishuChannel, FeishuConfig, from_mapping

channel = FeishuChannel.from_mapping({
    'app_id_ref': 'env:FEISHU_APP_ID',            # ⛔ 只写引用，不写值
    'app_secret_ref': 'env:FEISHU_APP_SECRET',    # ⛔ 只写引用，不写值
    'verification_token_ref': 'env:FEISHU_VERIFICATION_TOKEN',
    'encrypt_key_ref': 'env:FEISHU_ENCRYPT_KEY',
    'allowed_recipients': ['oc_xxx'],             # 收件人允许列表，空列表 = 不发
    'allowed_sender_open_ids': ['ou_xxx'],        # 谁能让人拍板
    'chat_bindings': {'oc_xxx': 'exp-01'},        # 显式 chat → 实验绑定
    'ledger_path': 'state/feishu_ledger.sqlite3',
    'send_enabled': False,                        # 默认不发，部署显式打开
})
```

⚠️ 默认 `send_enabled=False`：任何 `send_*` 都抛 `NotSent`（确定没发出去），
`health()` 返回 `ok=False`，核心（`DecisionService._assert_healthy`）因此在发送前就拦下。
"""

from __future__ import annotations

from .feishu import (ADAPTER, CLICK_INTENTS, SOURCE, WEBHOOK_PATH, AmbiguousSend,
                     FeishuChannel, FeishuConfig, FeishuConfigError, FeishuError,
                     NotAuthenticated, build_decision_card, compute_signature, env_ref,
                     from_mapping, make_http_handler, serve, urllib_transport)

__all__ = [
    'ADAPTER', 'CLICK_INTENTS', 'SOURCE', 'WEBHOOK_PATH',
    'FeishuChannel', 'FeishuConfig', 'FeishuConfigError', 'FeishuError',
    'NotAuthenticated', 'AmbiguousSend', 'build_decision_card', 'compute_signature',
    'env_ref', 'from_mapping', 'make_http_handler', 'serve', 'urllib_transport',
]
