# LAST-EDIT: 审计减负, 2026-11-09（三件事 + 最小报告；撤销七项 coverage / 逐字段 minor_fix_paths /
#   ok 项举证；来源只要求 path 可追溯；correct 支持自动小修 + `wording_only` 白名单；
#   `implementation.files` 绑定**磁盘实际字节**）
# CUMULATIVE-DELTA: 2026-11-09 审计减负与实现文件字节绑定。
# GENERATED-BY: framework synchronization, 2026-11-09
# PURPOSE: 审计叶子路径（实质 / 自动小修 / 可申报措辞）、实现文件字节、报告文件与短声明绑定（纯函数）。
# ORIGIN: generic mechanisms from plan_audit
import hashlib
import json
from pathlib import Path

#: 只影响解释、不改「验什么 / 怎么判 / 怎么跑」的说明文字叶子：自动小修。
AUTO_TEXT_LEAVES = frozenset({
    "why", "note", "rationale", "summary", "implication",
    "goal_link", "selection_reason", "mechanism_step", "text",
})
#: 整个子树都是解释的字段。
AUTO_TEXT_PREFIXES = ("open_questions",)
#: 实质路径的顶层：改它必须重新 prepare + 复审（说明文字另看 `wording_capable`）。
HARD_ROOTS = frozenset({
    "id", "goal", "hypothesis", "change_set", "criteria", "baseline",
    "case_sets", "previous_passes", "branches", "budget",
    "execution_argv", "implementation", "authorization", "execution",
})
#: 可申报 `wording_only` 的绑定字段说明文字（白名单，宁窄勿宽）。
WORDING_LEAVES = frozenset({"hypothesis", "hypothesis.text"})
#: 实现的五个说明字段（`implementation.*` 下的同名叶子）；实现文件路径与字节**不在内**。
IMPLEMENTATION_TEXT_FIELDS = frozenset({
    "expected_behavior", "load_path", "trigger", "observation", "non_activation",
})
#: `branches` 的字符串叶子（action / interpretation / when 等）。
WORDING_PREFIXES = ("branches.",)


def leaves(value, prefix=""):
    """把方案展平成 `{路径: 值}`（与源工具同口径：列表用 `.0` 下标）。"""
    if isinstance(value, dict) and value:
        out = {}
        for key, item in value.items():
            out.update(leaves(item, f"{prefix}.{key}" if prefix else str(key)))
        return out
    if isinstance(value, list) and value:
        out = {}
        for index, item in enumerate(value):
            out.update(leaves(item, f"{prefix}.{index}"))
        return out
    return {prefix: value}


def changes(before, after):
    left, right = leaves(before), leaves(after)
    return [key for key in sorted(set(left) | set(right))
            if key not in left or key not in right or left[key] != right[key]]


#: `locator` 仍算实质的根（执行参数 / 实现 / 授权：行号不能替它们换身份）。
LOCATOR_HARD_ROOTS = frozenset({"execution", "execution_argv", "implementation", "authorization"})


def hard_path(path):
    """实质路径：改它必须重新审计 + 复审；审计者**不能**再逐字段授权成小修。

    `criteria` 只有机器读项（mode / metric / direction / threshold / stop_condition）算实质；
    `criteria.source` 这类出处引用、`why` / `note` / `rationale` 属说明文字。
    `locator` 多数只算定位修正（`auto_minor` 永远放行）；只有执行 / 实现 / 授权根下仍算实质。
    """
    parts = path.split(".")
    if parts[0] == "criteria":
        return len(parts) < 2 or parts[1] in {
            "mode", "metric", "direction", "threshold", "stop_condition"}
    if parts[-1] == "locator":
        return parts[0] in LOCATOR_HARD_ROOTS
    return parts[0] in HARD_ROOTS


def auto_minor(path):
    """不必任何授权的小修：定位修正 / 证据引用换文件 / 普通说明文字。

    2026-11-09 减负后没有「审计者逐字段授权」这条路：不是自动小修、也不在
    `wording_only` 白名单的叶子，改了就必须重新 prepare + 复审。
    """
    leaf = path.rsplit(".", 1)[-1]
    if leaf == "locator":
        return True
    if path.split(".")[0] == "input_files":
        return True  # 证据引用换文件：只要求新路径可追溯
    if leaf == "source" and not hard_path(path):
        return True  # 出处引用（如 criteria.source）：可追溯即可
    if hard_path(path):
        return False
    return leaf in AUTO_TEXT_LEAVES or path.split(".")[0] in AUTO_TEXT_PREFIXES


def wording_capable(path):
    """允许在 `correct` 里用 `wording_only` 申报的绑定说明文字（白名单）。

    只钉「字段位置 + 前后原文」；⛔ 判不了语义。机器读项（判据 measure/threshold/kind、
    题单、`execution_argv`、实现文件路径与字节、装置身份、假设编号）**不在白名单**。
    """
    if path in WORDING_LEAVES:
        return True
    parts = path.split(".")
    if parts[0] == "implementation" and parts[-1] in IMPLEMENTATION_TEXT_FIELDS:
        return True
    return path.startswith(WORDING_PREFIXES)


def minor_path(path, claims=()):
    """这条叶子变了能不能记成小修：自动小修，或已被 `wording_only` 声明钉住。"""
    return auto_minor(path) or path in claims


def implementation_file_refs(plan):
    """`implementation.files` 里的相对路径（字符串或 `{path}` 都认）。"""
    implementation = plan.get("implementation") if isinstance(plan, dict) else None
    if not isinstance(implementation, dict):
        return []
    files = implementation.get("files")
    if not isinstance(files, list):
        return []
    out, seen = [], set()
    for item in files:
        path = item if isinstance(item, str) else (
            item.get("path") if isinstance(item, dict) else None)
        if not isinstance(path, str) or not path.strip():
            continue
        path = path.replace("\\", "/").strip()
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


def implementation_snapshot(root, plan):
    """实现文件的实际字节：`{相对路径: {sha256, bytes}}`。

    文件必须在工作区内且真实存在（读码可发现的错要在送审前排掉，⛔ 不留给运行期）。
    """
    root_path = Path(root).resolve()
    snapshot = {}
    for rel in implementation_file_refs(plan):
        candidate = (root_path / rel).resolve()
        if candidate != root_path and root_path not in candidate.parents:
            raise ValueError(f"实现文件必须在工作区内：{rel}")
        if not candidate.is_file():
            raise ValueError(f"实现文件不存在：{rel}")
        raw = candidate.read_bytes()
        snapshot[rel] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return snapshot


def report_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_bound_report(path, declaration, prepared_id=None):
    """核报告文件原始字节与六键短声明。真实消息来源仍由宿主 verifier 核验。

    报告可以省略 `prepared_id`（由调用方/程序生成的声明补），写了就必须一致；
    `report_file` 统一成解析后的绝对路径，`report_sha256` 按**原始字节**算。
    """
    raw = Path(path).read_bytes()
    report = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(report, dict):
        raise ValueError("审计报告必须是 JSON 对象")
    bound = report.get("prepared_id") or prepared_id
    if not bound:
        raise ValueError("报告缺 prepared_id，且调用方没有给被审材料标识")
    expected = {
        "prepared_id": bound,
        "decision": report["decision"],
        "decision_reason": report["decision_reason"],
        "report_file": str(Path(path).resolve()),
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "issues": [{"id": x["id"], "blocking": x["blocking"]} for x in report["issues"]],
    }
    if declaration != expected:
        raise ValueError("短声明与报告文件不一致")
    return report
