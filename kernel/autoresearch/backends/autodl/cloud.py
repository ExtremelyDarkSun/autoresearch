# GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
# LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
# PURPOSE: 云机操作适配（开机 / 关机 / 重启）—— **只有显式授权才会执行**；默认调用即拒绝。
# ORIGIN: 原项目把开机/关机放在外部 powershell 脚本里（`autodl.ps1 power_on|power_off`），内核只抽「调用形态」：
#         为什么必须显式授权 · 怎么用注入的传输层做离线验证 · 为什么「返回成功」不等于「真的开起来了」
# CUMULATIVE-DELTA: 首版。三条护栏写死在代码里：
#   ① 没有 `allow_control=True` ⇒ 直接 `PermissionError`，⛔ 不会发任何请求；
#   ② 传输层可注入 ⇒ 离线测试可以验证请求形态，而**不需要真的调云机**；
#   ③ 结果里 `status_confirmed` 恒为 False —— 本文件**不回读**机器状态，所以它只说明「请求发出去了」。
#   ⚠️ 本次抽取的任何测试都不得真的调用：测试只断言护栏与请求形态（用假传输层）。
"""云机操作适配（默认拒绝）。

⚠️⚠️ **这不是「读取状态」**：本文件会改变云上资源状态（可能产生费用）。
所以它有三条比别处更硬的规矩：

1. **必须显式授权**：调用方要传 `allow_control=True` 才可能发出请求；否则抛 `PermissionError`，一个字节都不发。
2. **凭据只用环境变量名或显式文件**：与 `availability` 同一套来源规则，⛔ 不内嵌、不打印。
3. **「请求成功」≠「机器可用」**：平台历史上出现过「返回成功但状态没变」。判「开机成功」的唯一口径是
   **回读实例状态**（`availability.collect()` 里 `status ∈ {running, starting}`），本文件不做这个回读，
   所以返回体的 `status_confirmed` 恒为 `False`。

⛔ 本内核的 worker 不会调用本文件；定时任务也不会。要用它，必须自己写一段显式授权的调用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import availability

TZ = timezone(timedelta(hours=8))

#: 允许的动作。⛔ 只收白名单里的值，避免把任意字符串拼进请求。
ALLOWED_ACTIONS = ("power_on", "power_off", "restart")


def power_action(machine_uuid: str, action: str, *,
                 api_url: str,
                 allow_control: bool = False,
                 token_env: "str | None" = None,
                 token_file: "str | None" = None,
                 transport=None,
                 timeout: float = availability.DEFAULT_TIMEOUT_S) -> dict:
    """对一台机器发一次动作请求。

    参数
    ----
    machine_uuid : 机器 uuid（不是机名；平台接口按 uuid 定位）。
    action       : `power_on` / `power_off` / `restart`。
    api_url      : 动作接口地址（部署方显式给）。
    allow_control: **必须显式 True**，否则抛 `PermissionError`（默认 False）。
    token_env / token_file : 凭据来源（环境变量名 / 显式文件），规则同 `availability.resolve_credential`。
    transport    : 传输层（`(url, body, headers, timeout) -> (status, body_bytes)`），离线测试用。
    timeout      : 单次请求超时秒数。

    返回
    ----
    `{"ok", "why", "action", "machine_uuid", "http_status", "status_confirmed": False, "at"}`
    `ok=True` 只表示「请求被接受」；**不表示机器已经开起来**。
    """
    if allow_control is not True:
        raise PermissionError(
            "云机操作需要显式授权：本函数不会在默认情况下发出任何请求。"
            "确需调用时传 allow_control=True，并自行确认授权范围。")
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"不支持的动作：{action}（只允许 {', '.join(ALLOWED_ACTIONS)}）")
    if not str(machine_uuid or "").strip():
        raise ValueError("machine_uuid 必填")
    if not str(api_url or "").strip():
        raise ValueError("api_url 必填")

    token, credential_ref, why = availability.resolve_credential(env_var=token_env, token_file=token_file)
    if why:
        return {"ok": False, "why": why, "action": action, "machine_uuid": machine_uuid,
                "http_status": None, "status_confirmed": False,
                "credential_ref": credential_ref,
                "at": datetime.now(TZ).isoformat(timespec="seconds")}

    body = json.dumps({"instance_uuid": machine_uuid, "action": action}).encode("utf-8")
    headers = availability.build_headers(token)
    send = transport or availability.urllib_transport
    try:
        status, raw = send(api_url, body, headers, float(timeout))
    except Exception as exc:  # noqa: BLE001 —— 失败要变成结构化结果，⛔ 不把凭据带进异常文本
        return {"ok": False, "why": f"请求失败：{availability.redact(str(exc))[:160]}",
                "action": action, "machine_uuid": machine_uuid, "http_status": None,
                "status_confirmed": False, "credential_ref": credential_ref,
                "at": datetime.now(TZ).isoformat(timespec="seconds")}

    ok = int(status) < 400
    reason = None
    if not ok:
        reason = f"接口返回 HTTP {status}"
    else:
        try:
            document = json.loads(raw.decode("utf-8", errors="replace"))
            code = str(document.get("code") or "")
            if code and code != "Success":
                ok = False
                reason = f"接口拒绝（{code}）：{availability.redact(str(document.get('msg') or ''))[:160]}"
        except ValueError:
            reason = "接口返回的不是可解析的 JSON（动作可能未生效）"
    return {
        "ok": ok,
        "why": reason,
        "action": action,
        "machine_uuid": machine_uuid,
        "http_status": int(status),
        # ⛔ 恒 False：本文件不回读状态，「请求被接受」不等于「机器已经开起来」。
        "status_confirmed": False,
        "credential_ref": credential_ref,
        "at": datetime.now(TZ).isoformat(timespec="seconds"),
    }
