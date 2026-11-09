# GENERATED-BY: ssh backend migration, 2026-11-09
# LAST-EDIT: 2026-11-10 安全修复：取消意向 + 跨进程锁、进程组停止证明、unknown 不冒终态、
#            run 记录与 request 的 hash/token/attempt 绑定；协议版本 2。
# PURPOSE: 在远端机器上执行一次 RunSpec：建孤立 code/env/input/output、核代码/题单/环境指纹真实字节、
#          起独立进程组、持久运行记录、硬超时、取消核 token+进程出生时间、按清单取回产物。
# ORIGIN: none（新文件）。对接口径见 kernel/autoresearch/backends/ssh.py；协议来自 runtime/execution.py 的
#         RunSpec / JobStatus / ResultBundle。
# CUMULATIVE-DELTA: 协议 v2。① start/supervise/cancel 用跨进程文件锁 + `cancel.json` 意向标记互斥，
#          无 pid 的取消只记意向、⛔ 不报 cancelled，且阻止之后的晚启动；② 任何终态都必须先证明
#          「原进程及其整个进程组已无活进程」（POSIX 读 /proc，退回 ps；Windows 不能枚举 ⇒ 只能 unknown）；
#          ③ 硬超时 TERM→KILL 后组未停 ⇒ unknown（保留 pid/birth 供再次取消），⛔ 不报 failed；
#          ④ query/cancel/collect 先把 state.json 与 request.json 的 request_hash/token/attempt_id/spec/
#          output_dir/argv 对齐，再回报；⑤ 停止已核但运行记录写不进去时仍如实回报停止（报告不挡安全停）。
#          ⛔ 不回滚 `_probe_python` 的 importlib.metadata 真实包指纹与独立环境 prefix 核验。
"""SSH 远端 worker（自包含，只用标准库）。

这个文件会被 `ssh.py` 的 `provision()` **原样推到远端**，然后用
`python3 <worker.py> <subcommand> --state-dir <dir>` 调用。它不 import 本仓库任何模块，
以免远端缺包。

远端目录布局（全部由 RunSpec 指定，worker 会重新校验不越界）::

    <unit_root>/
      code/             # code_root：要跑的实现
      env/              # environment_root：venv 或指向现成独立环境
      input/            # input_root：题单 manifest + 共享输入
      output/           # output_root：每次 attempt 一个子目录
      .ssh_worker/<request_hash>/   # 运行记录（request.json / state.json / cancel.json / lock / 日志）

运行记录 `state.json` 的字段（原子写）::

    request_hash, attempt_id, state, token, pid, birth, started_at, finished_at,
    timeout_s, argv, spec, environment, output_dir, exitcode, signal, timed_out,
    reason, protocol, cancel_requested, stop_verified, stop_evidence

协议版本 2 的停止语义
=====================

* **取消意向先落盘**：`cmd_cancel` 先写 `.ssh_worker/<hash>/cancel.json`（绑定
  request_hash/token/attempt_id），再决定能不能停。**没有 pid 不等于已停**：只记意向并返回
  `unknown`，由 `cmd_start`/`cmd_supervise` 在跨进程锁内看到意向、**拒绝晚启动**并记
  `cancelled`（`stop_verified=true`，method=`never-launched`）。
* **锁**：`cmd_start`/`cmd_supervise` 的「检查意向 → 写 STARTING → spawn」与 `cmd_cancel`
  的「读记录 → 写意向 → 发信号」都在 `<state_dir>/lock` 的排他锁内，跨进程互斥。
* **终态必须证明整组已停**：`completed`/`failed`/`cancelled` 都要求
  `stop_verified=true`。POSIX 枚举 `/proc`（退回 `ps`）确认原进程组里没有活进程（僵尸不算活）；
  主进程 exit 0 但同组还有活子进程 ⇒ `unknown`，保留 pid/birth，⛔ 不写 completed。
* **组的边界**：证明覆盖的是这次 job 的进程组（pgid=leader pid）。子进程若自己 `setsid`
  另起组/会话，worker 从组 id 上看不到它；这类逃逸不在本证明范围内（要更强保证需远端
  cgroup/namespace 支撑）。
* **PID 复用**：取消必须先核 token 与出生时间；出生时间未知或已变 ⇒ ⛔ 不发信号，只报 `unknown`
  （组已空时 `pid-reused-original-gone` 才可记 cancelled）。
* **超时失败不冒充停止**：TERM → KILL 之后组仍在（或本平台不能枚举）⇒ `unknown`，
  `timed_out=true` 但不写 failed。
* **未知不冒终态**：没有终态记录、进程查不到、记录损坏、连接中断、request/state 不一致 ⇒ `unknown`。
* **指纹核真实字节**：代码按整棵树的 `{相对路径: sha256}` 重算；题单按
  `input/manifest.json` 里逐题文件重算；环境按解释器字节 + 版本 + **importlib.metadata 真实包清单**
  + 独立 env prefix 重算。⛔ 不认 `isolated_environment=True` 这种布尔声明。
* **硬超时在远端**：由 supervisor 进程计时并杀整个进程组，不是靠本地 SSH 超时。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

WORKER_VERSION = 2
PROTOCOL_FEATURES = ("request-binding", "cancel-intent-lock", "process-group-proof",
                     "argv-digest")
STATE_STARTING = "starting"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_UNKNOWN = "unknown"
FINAL_STATES = frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED})
KNOWN_STATES = frozenset({STATE_STARTING, STATE_RUNNING, *FINAL_STATES})
GRACE_SECONDS = 3.0
START_WAIT_SECONDS = 15.0
LOCK_WAIT_SECONDS = 30.0
LOCK_POLL_SECONDS = 0.02
GROUP_POLL_SECONDS = 0.1
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class WorkerError(RuntimeError):
    """worker 拒绝执行。`definitive=True` 表示重试也不会变（请求/指纹/环境不合规）。"""

    def __init__(self, message, *, definitive=False):
        super().__init__(message)
        self.definitive = bool(definitive)


# ---------------------------------------------------------------------------
# 基本工具：canonical / 哈希 / 安全路径
# ---------------------------------------------------------------------------


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path) -> str:
    with open(path, "rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root) -> dict[str, str]:
    """整棵树 -> `{相对路径: sha256}`。⛔ 符号链接一律拒绝（无法保证指向同一份字节）。"""
    root = Path(root)
    if not root.is_dir():
        raise WorkerError(f"代码目录不存在或不是目录：{root}", definitive=True)
    manifest: dict[str, str] = {}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        files.sort()
        for name in files:
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise WorkerError(f"代码目录含符号链接，拒绝按它核指纹：{rel}", definitive=True)
            if not path.is_file():
                raise WorkerError(f"代码目录含非普通文件：{rel}", definitive=True)
            manifest[rel] = sha_file(path)
    return manifest


def tree_fingerprint(root) -> str:
    return sha_bytes(canonical(tree_manifest(root)).encode("utf-8"))


def _safe_rel(value) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    return (not value.startswith("/")) and posixpath.normpath(value) == value \
        and value not in {".", ".."} and not value.startswith("../")


def safe_join(root, rel) -> Path:
    if not _safe_rel(rel):
        raise WorkerError(f"不安全的相对路径：{rel!r}", definitive=True)
    base = Path(root).resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise WorkerError(f"路径逃逸：{rel!r}", definitive=True)
    return target


def _safe_task_id(value) -> bool:
    return isinstance(value, str) and bool(TASK_ID_RE.match(value)) and value not in {".", ".."}


def _abs_path(value) -> bool:
    """绝对路径判定。

    生产远端是 POSIX：只接受以 `/` 开头的规范化路径。**Windows 分支只用于离线测试**
    （真实远端仍是 POSIX），因此仍要求绝对、规范化，不放宽相对路径。
    """
    if not isinstance(value, str) or not value:
        return False
    if value.startswith("/"):
        return posixpath.normpath(value) == value and value != "/"
    if os.name != "posix":
        return Path(value).is_absolute() and os.path.normpath(value) == value
    return False


def _under(child, parent) -> bool:
    try:
        child_path = Path(child).resolve()
        parent_path = Path(parent).resolve()
    except OSError:
        return False
    return child_path == parent_path or parent_path in child_path.parents


def _overlap(left, right) -> bool:
    return _under(left, right) or _under(right, left)


def _posix_abs(value) -> bool:  # 兼容旧名；语义见 _abs_path
    return _abs_path(value)


# ---------------------------------------------------------------------------
# 请求 / 状态文件（原子写）
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + secrets.token_hex(6))
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _write_json(path: Path, value) -> None:
    _atomic_write(path, (canonical(value) + "\n").encode("utf-8"))


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def request_payload(request: dict) -> dict:
    return {key: request.get(key) for key in
            ("spec", "argv", "timeout_s", "environment", "python")}


def request_hash(request: dict) -> str:
    return sha_bytes(canonical(request_payload(request)).encode("utf-8"))


def validate_request(request: dict) -> dict:
    if not isinstance(request, dict):
        raise WorkerError("request 必须是对象", definitive=True)
    spec = request.get("spec")
    if not isinstance(spec, dict):
        raise WorkerError("request.spec 必须是对象", definitive=True)
    for key in ("unit_root", "code_root", "environment_root", "input_root", "output_root"):
        if not _abs_path(spec.get(key)):
            raise WorkerError(f"spec.{key} 必须是规范化绝对路径", definitive=True)
    unit = spec["unit_root"]
    for key in ("code_root", "environment_root", "input_root", "output_root"):
        if not _under(spec[key], unit):
            raise WorkerError(f"spec.{key} 必须在 unit_root 内", definitive=True)
    roots = [spec[k] for k in ("code_root", "environment_root", "input_root", "output_root")]
    for index, left in enumerate(roots):
        for right in roots[index + 1:]:
            if _overlap(left, right):
                raise WorkerError("代码/环境/输入/输出路径重叠", definitive=True)
    task_ids = spec.get("task_ids")
    if not isinstance(task_ids, list) or not task_ids \
            or not all(_safe_task_id(item) for item in task_ids) \
            or len(set(task_ids)) != len(task_ids):
        raise WorkerError("spec.task_ids 必须是非空、唯一、安全文件名的题号", definitive=True)
    artifacts = spec.get("required_artifacts")
    if not isinstance(artifacts, list) or not artifacts \
            or not all(_safe_rel(item) for item in artifacts) \
            or len(set(artifacts)) != len(artifacts):
        raise WorkerError("spec.required_artifacts 必须是安全相对路径清单", definitive=True)
    argv = request.get("argv")
    if not isinstance(argv, list) or not argv \
            or any(not isinstance(item, str) or not item for item in argv):
        raise WorkerError("request.argv 必须是非空字符串数组", definitive=True)
    timeout = request.get("timeout_s")
    if type(timeout) not in (int, float) or not (0 < float(timeout) <= 7 * 24 * 3600):
        raise WorkerError("request.timeout_s 必须是 (0, 7天] 的有限数", definitive=True)
    token = request.get("token")
    if token is not None and (not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32,64}", token)):
        raise WorkerError("request.token 必须是 32–64 位小写十六进制", definitive=True)
    output = request.get("output_dir")
    if not _abs_path(output) or not _under(output, spec["output_root"]):
        raise WorkerError("request.output_dir 必须是 output_root 下的绝对路径", definitive=True)
    if request.get("request_hash") and request["request_hash"] != request_hash(request):
        raise WorkerError("request_hash 与请求内容不符", definitive=True)
    request["request_hash"] = request_hash(request)
    return request


def state_dir_for(unit_root: str, digest: str) -> str:
    return posixpath.join(unit_root, ".ssh_worker", digest[:32])


def _state_paths(state_dir) -> tuple[Path, Path, Path]:
    base = Path(state_dir)
    return base, base / "request.json", base / "state.json"


# ---------------------------------------------------------------------------
# 跨进程互斥 / 取消意向 / 运行记录与请求的绑定
# ---------------------------------------------------------------------------


def _lock_handle(handle) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock_handle(handle) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    else:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def state_lock(state_dir, *, timeout: float = LOCK_WAIT_SECONDS):
    """`<state_dir>/lock` 的跨进程排他锁。

    「检查取消意向 → 写 STARTING → spawn supervisor」与「读记录 → 写意向 → 发信号」必须互斥，
    否则取消会看到「还没有 pid」而放行一次晚启动。锁只保护状态转换，不保护耗时的指纹计算与等待。
    """
    path = Path(state_dir) / "lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    locked = False
    try:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.time() + max(float(timeout), 0.0)
        while True:
            try:
                _lock_handle(handle)
                locked = True
                break
            except OSError:
                if time.time() >= deadline:
                    raise WorkerError("等待 state_dir 跨进程锁超时，状态不可判") from None
                time.sleep(LOCK_POLL_SECONDS)
        yield
    finally:
        if locked:
            try:
                _unlock_handle(handle)
            except OSError:
                pass
        handle.close()


def cancel_marker_path(state_dir) -> Path:
    return Path(state_dir) / "cancel.json"


def _marker_status(state_dir, state) -> str | None:
    """`None`=无标记；`valid`=与运行记录对得上；`invalid`=有标记但内容对不上。"""
    path = cancel_marker_path(state_dir)
    if not path.is_file():
        return None
    try:
        document = _read_json(path)
    except (OSError, ValueError):
        return "invalid"
    if not isinstance(document, dict):
        return "invalid"
    state = state if isinstance(state, dict) else {}
    if document.get("request_hash") != state.get("request_hash"):
        return "invalid"
    token = document.get("token")
    if not isinstance(token, str) or not token or token != state.get("token"):
        return "invalid"
    expected_attempt = state.get("attempt_id")
    if expected_attempt is not None and document.get("attempt_id") != expected_attempt:
        return "invalid"
    return "valid"


def _launch_block(state_dir, state) -> dict | None:
    """启动前的取消闸。返回 `None` 才允许 spawn。

    先看已核过的 `cancel_requested`（取消方已核 token），再看落盘的意向标记。标记对不上时
    **两个方向都不许动**：既不启动，也不宣称已停，只记 `unknown`。
    """
    state = state if isinstance(state, dict) else {}
    if state.get("cancel_requested") is True:
        return {"state": STATE_CANCELLED, "reason": "cancel-before-launch",
                "stop_verified": True,
                "stop_evidence": {"method": "never-launched", "cancel_requested": True}}
    status = _marker_status(state_dir, state)
    if status == "valid":
        return {"state": STATE_CANCELLED, "reason": "cancel-before-launch",
                "stop_verified": True,
                "stop_evidence": {"method": "never-launched", "cancel_marker": True}}
    if status == "invalid":
        return {"state": STATE_UNKNOWN,
                "reason": "取消意向标记与运行记录不一致：拒绝启动，也不宣称已停",
                "stop_verified": False,
                "stop_evidence": {"method": "cancel-marker-mismatch"}}
    return None


def _blocked_state(state: dict, block: dict, *, at: float | None = None) -> dict:
    merged = dict(state)
    merged.update(state=block["state"],
                  finished_at=time.time() if at is None else at,
                  stop_verified=block.get("stop_verified") is True,
                  stop_evidence=block.get("stop_evidence"),
                  cancel_requested=True,
                  reason=block.get("reason"))
    return merged


def binding_reason(request, state) -> str | None:
    """运行记录是否就是这次请求：hash / token / attempt / spec / output_dir / argv / timeout。

    不通过 ⇒ 上层只能报 `unknown`（query）或拒绝（collect），⛔ 不把别的请求的状态当成这一次的。
    """
    if not isinstance(request, dict) or not isinstance(state, dict):
        return "request 或 state 不是对象"
    if state.get("request_hash") != request.get("request_hash"):
        return "state 的 request_hash 与 request.json 不符"
    token = request.get("token")
    if token is not None and state.get("token") != token:
        return "state 的 token 与 request.json 不符"
    attempt = request.get("attempt_id")
    if attempt is not None and state.get("attempt_id") != attempt:
        return "state 的 attempt_id 与 request.json 不符"
    if canonical(state.get("spec")) != canonical(request.get("spec")):
        return "state 的 spec 与 request.json 不符"
    if state.get("output_dir") != request.get("output_dir"):
        return "state 的 output_dir 与 request.json 不符"
    try:
        if float(state.get("timeout_s")) != float(request.get("timeout_s")):
            return "state 的 timeout_s 与 request.json 不符"
    except (TypeError, ValueError):
        return "state 的 timeout_s 不可比"
    environment = state.get("environment")
    if not isinstance(environment, dict) or not isinstance(environment.get("python"), str):
        return "state 缺已解析的 environment（不能重算 argv）"
    if state.get("argv") != launch_argv(request, environment):
        return "state 的 argv 与 request.json 不符"
    return None


# ---------------------------------------------------------------------------
# 指纹：代码 / 题单 / 环境
# ---------------------------------------------------------------------------


def code_fingerprint(code_root) -> str:
    return tree_fingerprint(code_root)


def task_manifest(input_root) -> dict[str, str]:
    path = Path(input_root) / "manifest.json"
    if not path.is_file():
        raise WorkerError("input_root 缺 manifest.json（题单清单）", definitive=True)
    try:
        document = _read_json(path)
    except (OSError, ValueError) as exc:
        raise WorkerError(f"题单 manifest.json 不可读：{exc}", definitive=True) from exc
    if not isinstance(document, dict) or set(document) != {"tasks"} \
            or not isinstance(document.get("tasks"), dict):
        raise WorkerError("manifest.json 必须形如 {'tasks': {id: {path, sha256}}}", definitive=True)
    manifest: dict[str, str] = {}
    for task_id, item in document["tasks"].items():
        if not _safe_task_id(task_id):
            raise WorkerError(f"题号不安全：{task_id!r}", definitive=True)
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise WorkerError(f"题单 {task_id} 必须给 path + sha256", definitive=True)
        target = safe_join(input_root, item["path"])
        if not target.is_file():
            raise WorkerError(f"题单 {task_id} 的文件不存在：{item['path']}", definitive=True)
        actual = sha_file(target)
        if actual != item["sha256"]:
            raise WorkerError(f"题单 {task_id} 的字节与清单不符", definitive=True)
        manifest[task_id] = actual
    return manifest


def task_fingerprint(input_root) -> str:
    return sha_bytes(canonical(task_manifest(input_root)).encode("utf-8"))


def _probe_python(python: str) -> dict:
    script = ("import json,sys;from importlib import metadata;"
              "print(json.dumps({'version':sys.version.split()[0],"
              "'prefix':sys.prefix,'base_prefix':getattr(sys,'base_prefix',sys.prefix),"
              "'executable':sys.executable,'packages':sorted("
              "str(d.metadata.get('Name',''))+'=='+str(d.version) for d in metadata.distributions())}))")
    try:
        result = subprocess.run([python, "-I", "-c", script], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=30, shell=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorkerError(f"解释器不可执行：{python}：{exc}", definitive=True) from exc
    if result.returncode != 0:
        raise WorkerError(f"解释器探针失败：{python}：{result.stderr.decode('utf-8', 'replace')[:300]}",
                          definitive=True)
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except ValueError as exc:
        raise WorkerError(f"解释器探针输出不是 JSON：{exc}", definitive=True) from exc


def environment_fingerprint(python: str, packages, pyvenv_cfg) -> str:
    """环境指纹：解释器字节 + 版本 + 包清单（+ venv 的 pyvenv.cfg 字节）。"""
    probe = _probe_python(python)
    document = {
        "python_sha256": sha_file(python),
        "version": probe["version"],
        "packages": probe['packages'],
        "requested_packages": sorted(str(item) for item in (packages or [])),
        "pyvenv_sha256": sha_file(pyvenv_cfg) if pyvenv_cfg else None,
    }
    return sha_bytes(canonical(document).encode("utf-8"))


def _venv_python(environment_root) -> tuple[str, str, str]:
    root = Path(environment_root)
    candidates = [root / "bin" / "python3", root / "bin" / "python",
                  root / "Scripts" / "python.exe", root / "python.exe"]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate), str(root / "pyvenv.cfg"), str(root)
    raise WorkerError(f"environment_root 里找不到解释器（bin/python3 或 Scripts/python.exe）：{root}",
                      definitive=True)


def resolve_environment(request: dict) -> dict:
    """独立环境：venv 或经 fingerprint 核过的现成独立环境。⛔ 不认布尔隔离声明。"""
    spec = request["spec"]
    config = request.get("environment")
    if config is None:
        config = {"kind": "venv"}
    if not isinstance(config, dict) or config.get("kind") not in ("venv", "existing"):
        raise WorkerError("environment 必须是 {'kind': 'venv'|'existing', ...}", definitive=True)
    unit_root = spec["unit_root"]
    if config["kind"] == "venv":
        python, pyvenv_cfg, prefix = _venv_python(spec["environment_root"])
        if not Path(pyvenv_cfg).is_file():
            raise WorkerError("environment_root 不是 venv：缺 pyvenv.cfg", definitive=True)
        packages = config.get("packages") or []
        fingerprint = environment_fingerprint(python, packages, pyvenv_cfg)
    else:
        python = config.get("python")
        if not _abs_path(python):
            raise WorkerError("existing 环境的 python 必须是规范化绝对路径", definitive=True)
        if not _under(python, spec['environment_root']):
            raise WorkerError('existing解释器必须位于本次独立environment_root', definitive=True)
        probe = _probe_python(python)
        prefix = probe["prefix"]
        if os.path.abspath(prefix) != os.path.abspath(spec['environment_root']):
            raise WorkerError('解释器实际sys.prefix与独立环境根不一致', definitive=True)
        pyvenv_cfg = str(Path(prefix) / "pyvenv.cfg")
        if not (Path(pyvenv_cfg).is_file() or probe["prefix"] != probe["base_prefix"]):
            raise WorkerError("指定的解释器不是独立环境（prefix == base_prefix 且无 pyvenv.cfg）",
                              definitive=True)
        if not Path(pyvenv_cfg).is_file():
            pyvenv_cfg = ""
        packages = config.get("packages") or []
        fingerprint = environment_fingerprint(python, packages, pyvenv_cfg or None)
    actual_probe = _probe_python(python)
    if os.path.abspath(actual_probe['prefix']) != os.path.abspath(spec['environment_root']) or actual_probe['prefix'] == actual_probe['base_prefix']:
        raise WorkerError('真实解释器不属于本次独立环境', definitive=True)
    if fingerprint != spec.get("environment_fingerprint"):
        raise WorkerError("环境指纹与 RunSpec 不符（解释器/版本/包清单变了）", definitive=True)
    return {"python": python, "prefix": prefix, "fingerprint": fingerprint,
            "packages": sorted(str(item) for item in packages)}


def _shared_input_fingerprint(item: dict) -> str:
    path = Path(item["path"])
    if path.is_dir():
        return tree_fingerprint(path)
    if path.is_file():
        return sha_file(path)
    raise WorkerError(f"共享输入不存在：{item['path']}", definitive=True)


def verify_shared_inputs(spec: dict) -> None:
    mutable = [spec["code_root"], spec["environment_root"], spec["output_root"]]
    for index, item in enumerate(spec.get("shared_inputs") or []):
        if not isinstance(item, dict) or set(item) != {"path", "fingerprint", "read_only", "immutable"}:
            raise WorkerError(f"shared_inputs[{index}] 形状不对", definitive=True)
        if item["read_only"] is not True or item["immutable"] is not True:
            raise WorkerError(f"shared_inputs[{index}] 必须只读且不变", definitive=True)
        if not _abs_path(item["path"]):
            raise WorkerError(f"shared_inputs[{index}].path 必须是绝对路径", definitive=True)
        if any(_overlap(item["path"], root) for root in mutable):
            raise WorkerError("共享输入不得覆盖可变代码/环境/输出", definitive=True)
        if _shared_input_fingerprint(item) != item["fingerprint"]:
            raise WorkerError(f"shared_inputs[{index}] 字节与指纹不符", definitive=True)


# ---------------------------------------------------------------------------
# 进程：出生时间 / 存活 / 进程组
# ---------------------------------------------------------------------------


def process_birth(pid: int):
    """进程出生时间：POSIX 读 /proc/<pid>/stat 第 22 字段；Windows 用 GetProcessTimes。"""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "posix":
        try:
            data = Path(f"/proc/{pid}/stat").read_bytes().decode("utf-8", "replace")
            rest = data.rsplit(")", 1)[1].split()
            return int(rest[19])
        except (OSError, IndexError, ValueError):
            return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - 取不到就是不可判
        return None


def process_alive(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


def _term_group(pid: int) -> None:
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                return
    else:
        subprocess.run(["taskkill", "/PID", str(pid), "/T"], shell=False,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)


def _kill_group(pid: int) -> None:
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                return
    else:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], shell=False,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)


def _proc_group_members(pgid: int):
    """读 `/proc/<pid>/stat` 的 pgrp（第 5 字段）列出组内**活**进程；/proc 不可用 ⇒ None。"""
    root = Path("/proc")
    try:
        entries = os.listdir(root)
    except OSError:
        return None
    members = set()
    for name in entries:
        if not name.isdigit():
            continue
        try:
            data = (root / name / "stat").read_bytes().decode("utf-8", "replace")
            rest = data.rsplit(")", 1)[1].split()
            state = rest[0]
            group = int(rest[2])
        except (OSError, IndexError, ValueError):
            continue
        if group == pgid and state != "Z":  # 僵尸已死，不算「还在跑」
            members.add(int(name))
    return members


def _ps_group_members(pgid: int):
    """`/proc` 不可用时退回 `ps -eo pid=,pgid=,stat=`；失败 ⇒ None。"""
    try:
        result = subprocess.run(["ps", "-eo", "pid=,pgid=,stat="],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10, shell=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    members = set()
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            member_pid, group = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if group == pgid and not parts[2].startswith("Z"):
            members.add(member_pid)
    return members


def group_members(pgid: int):
    """原进程组里仍活着的 pid 集合。

    `None` = **本平台不能枚举**（Windows 没有等价内核接口），上层只能报 `unknown`：
    ⛔ 不许把「主进程不在」当成「整个组已停」。
    """
    if not isinstance(pgid, int) or pgid <= 0:
        return set()
    if os.name != "posix":
        return None
    members = _proc_group_members(pgid)
    if members is not None:
        return members
    return _ps_group_members(pgid)


def group_gone(pgid: int):
    """True=组里已无活进程；False=还有；None=不能证明。"""
    members = group_members(pgid)
    if members is None:
        return None
    return not members


def _wait_group_gone(pid: int, seconds: float):
    deadline = time.time() + max(float(seconds), 0.0)
    while True:
        gone = group_gone(pid)
        if gone is not False:
            return gone
        remaining = deadline - time.time()
        if remaining <= 0:
            return gone
        time.sleep(min(GROUP_POLL_SECONDS, remaining))


def _stop_group(pid: int, birth, grace: float | None = None) -> dict:
    """对一个进程组做 TERM → KILL，并**重新核**整组是否已停。

    返回 `{"verified", "method", "evidence"}`。`verified=False` 表示不能证明已停，
    调用方必须报 `unknown`，⛔ 不许写 failed/cancelled 终态。
    """
    grace = GRACE_SECONDS if grace is None else float(grace)
    evidence = {"pid": pid, "birth": birth, "signals": [], "method": None,
                "members_after": None}
    current = process_birth(pid)
    if birth is not None and current is not None and current != birth:
        # PID 被复用：⛔ 绝不向新进程/新组发信号；只有组已空才能说原 job 的历史已经结束。
        gone = group_gone(pid)
        evidence.update(method="identity-changed", identity_changed=True,
                        members_after=(None if gone is None else (0 if gone else "unknown")))
        return {"verified": gone is True, "method": "identity-changed", "evidence": evidence}
    initial = group_gone(pid)
    if initial is True:
        evidence.update(method="already-gone", members_after=0)
        return {"verified": True, "method": "already-gone", "evidence": evidence}
    _term_group(pid)
    evidence["signals"].append("TERM")
    gone = _wait_group_gone(pid, grace)
    if gone is True:
        evidence.update(method="term", members_after=0)
        return {"verified": True, "method": "term", "evidence": evidence}
    _kill_group(pid)
    evidence["signals"].append("KILL")
    gone = _wait_group_gone(pid, grace)
    evidence["members_after"] = None if gone is None else (0 if gone else "alive")
    if gone is True:
        evidence.update(method="kill", members_after=0)
        return {"verified": True, "method": "kill", "evidence": evidence}
    evidence["method"] = "kill-unverified"
    return {"verified": False, "method": "kill-unverified", "evidence": evidence}


def _wait_gone(pid: int, birth, seconds: float) -> bool:
    """兼容旧名：只等待 + 复核，**不发信号**；只有整组都停了才返回 True（不能证明 ⇒ False）。"""
    current = process_birth(pid)
    if birth is not None and current is not None and current != birth:
        return group_gone(pid) is True
    return _wait_group_gone(pid, seconds) is True


def _child_env(environment: dict) -> dict:
    env = dict(os.environ)
    prefix = environment["prefix"]
    bin_dir = str(Path(prefix) / ("Scripts" if os.name != "posix" else "bin"))
    env["VIRTUAL_ENV"] = prefix
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    env["PYTHONNOUSERSITE"] = "1"
    for key in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    return env


def launch_argv(request: dict, environment: dict) -> list[str]:
    """原始 argv 来自 execution_config；只把首项是 python/python3 的换成独立解释器。"""
    argv = list(request["argv"])
    first = os.path.basename(argv[0]).lower()
    if first in {"python", "python3", "python.exe", "python3.exe"}:
        argv[0] = environment["python"]
    return argv


# ---------------------------------------------------------------------------
# 命令：request-write / start / query / cancel / collect / supervise
# ---------------------------------------------------------------------------


def _request_reply(request: dict, state_dir: Path, *, reused: bool) -> dict:
    return {"ok": True, "state_dir": str(state_dir),
            "request_hash": request["request_hash"],
            "token": request.get("token"), "attempt_id": request.get("attempt_id"),
            "protocol": WORKER_VERSION, "reused": reused}


def cmd_request_write(args) -> dict:
    state_dir = Path(args.state_dir)
    raw = sys.stdin.buffer.read()
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WorkerError(f"request 不是合法 JSON：{exc}", definitive=True) from exc
    request = validate_request(request)
    spec = request["spec"]
    if not _under(str(state_dir), spec["unit_root"]):
        raise WorkerError("state_dir 必须位于 unit_root 内", definitive=True)
    request_path = state_dir / "request.json"
    try:
        state_dir.mkdir(parents=True)
    except FileExistsError:
        if not request_path.is_file():
            raise WorkerError("state_dir 已存在但没有 request.json（不可判）")
        previous = _read_json(request_path)
        if previous.get("request_hash") != request["request_hash"]:
            raise WorkerError("同一 request_key 已登记过不同的请求内容，拒绝重发", definitive=True)
        return _request_reply(request, state_dir, reused=True)
    _write_json(request_path, request)
    return _request_reply(request, state_dir, reused=False)


def _load_request(state_dir: Path) -> dict:
    request_path = Path(state_dir) / "request.json"
    if not request_path.is_file():
        raise WorkerError("没有 request.json：这个请求从未登记", definitive=True)
    request = validate_request(_read_json(request_path))
    if request_hash(request) != request["request_hash"]:
        raise WorkerError("request.json 内容被改过", definitive=True)
    return request


def _read_request_optional(state_dir):
    """取消用：request.json 损坏/缺失都**不能挡安全停**；⛔ 也不能因此多信一分。"""
    try:
        return _load_request(state_dir)
    except (WorkerError, OSError, ValueError):
        return None


def _read_state_optional(state_path: Path):
    if not Path(state_path).is_file():
        return None, None
    try:
        state = _read_json(state_path)
    except (OSError, ValueError) as exc:
        return None, f"state.json 不可读：{exc}"
    if not isinstance(state, dict):
        return None, "state.json 不是对象"
    return state, None


def _argv_sha256(value):
    """实际启动 argv 的摘要（sha256(canonical(argv))）；⛔ 不回显完整 argv，避免参数里的 secret。"""
    if not isinstance(value, list) or not value \
            or not all(isinstance(item, str) for item in value):
        return None
    return sha_bytes(canonical(value).encode("utf-8"))


def _summarise(state: dict) -> dict:
    keys = ("request_hash", "attempt_id", "state", "pid", "birth", "started_at",
            "finished_at", "exitcode", "signal", "timed_out", "token", "reason",
            "output_dir", "timeout_s", "protocol", "cancel_requested",
            "stop_verified", "stop_evidence")
    summary = {key: state.get(key) for key in keys}
    summary["argv_sha256"] = _argv_sha256(state.get("argv"))
    return summary


def _popen(argv, **kwargs):
    """唯一 spawn 入口：测试可以替换它，⛔ 真实路径里不许别处直接 Popen。"""
    return subprocess.Popen(argv, **kwargs)


def cmd_start(args) -> dict:
    state_dir = Path(args.state_dir)
    request = _load_request(state_dir)
    state_path = state_dir / "state.json"
    existing, state_error = _read_state_optional(state_path)
    if existing is not None:
        reason = binding_reason(request, existing)
        if reason:
            raise WorkerError(f"state.json 与 request.json 不一致：{reason}", definitive=True)
        if existing.get("state") in KNOWN_STATES or existing.get("state") == STATE_UNKNOWN:
            return {"ok": True, "state": _summarise(existing), "reused": True}
    spec = request["spec"]
    verify_shared_inputs(spec)
    actual_code = code_fingerprint(spec["code_root"])
    if actual_code != spec["code_fingerprint"]:
        raise WorkerError("代码指纹与 RunSpec 不符（真实字节变了）", definitive=True)
    manifest = task_manifest(spec["input_root"])
    if set(manifest) != set(spec["task_ids"]):
        raise WorkerError("题单清单与 spec.task_ids 不一致", definitive=True)
    actual_tasks = sha_bytes(canonical(manifest).encode("utf-8"))
    if actual_tasks != spec["task_fingerprint"]:
        raise WorkerError("题单指纹与 RunSpec 不符（真实字节变了）", definitive=True)
    environment = resolve_environment(request)
    output_dir = Path(request["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "request_hash": request["request_hash"],
        "attempt_id": request.get("attempt_id"),
        "state": STATE_STARTING,
        "token": request.get("token") or secrets.token_hex(24),
        "pid": None,
        "birth": None,
        "started_at": None,
        "finished_at": None,
        "timeout_s": float(request["timeout_s"]),
        "argv": launch_argv(request, environment),
        "spec": spec,
        "environment": environment,
        "output_dir": request["output_dir"],
        "exitcode": None,
        "signal": None,
        "timed_out": False,
        "reason": None,
        "protocol": WORKER_VERSION,
        "cancel_requested": False,
        "stop_verified": False,
        "stop_evidence": None,
    }
    with state_lock(state_dir):
        current, _ = _read_state_optional(state_path)
        if current is not None:
            reason = binding_reason(request, current)
            if reason:
                raise WorkerError(f"state.json 与 request.json 不一致：{reason}", definitive=True)
            if current.get("state") in KNOWN_STATES or current.get("state") == STATE_UNKNOWN:
                return {"ok": True, "state": _summarise(current), "reused": True}
        block = _launch_block(state_dir, state if current is None else current)
        if block is not None:
            blocked = _blocked_state(state, block)
            _write_json(state_path, blocked)
            return {"ok": True, "state": _summarise(blocked), "reused": False,
                    "cancelled": blocked.get("state") == STATE_CANCELLED,
                    "reason": blocked.get("reason")}
        _write_json(state_path, state)
        supervisor_log = state_dir / "supervisor.log"
        with open(supervisor_log, "ab") as log:
            popen_kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
                            "close_fds": True, "shell": False}
            if os.name == "posix":
                popen_kwargs["start_new_session"] = True
            else:
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            _popen([sys.executable, str(Path(__file__).resolve()),
                    "supervise", "--state-dir", str(state_dir)], **popen_kwargs)
    deadline = time.time() + START_WAIT_SECONDS
    while time.time() < deadline:
        current, error = _read_state_optional(state_path)
        if current is None:
            if error:
                break
            time.sleep(0.1)
            continue
        if current.get("state") in KNOWN_STATES and current.get("state") != STATE_STARTING:
            return {"ok": True, "state": _summarise(current), "reused": False}
        if current.get("state") == STATE_STARTING and current.get("pid"):
            return {"ok": True, "state": _summarise(current), "reused": False}
        time.sleep(0.1)
    current, error = _read_state_optional(state_path)
    if current is None:
        return {"ok": True, "state": {"state": STATE_UNKNOWN,
                                      "reason": error or "启动后 state.json 不可读"},
                "reused": False}
    return {"ok": True, "state": _summarise(current), "reused": False,
            "warning": "supervisor 未在等待窗口内报告 pid；按不可判处理，后续用 query 复核"}


def _unknown_view(state, reason: str) -> dict:
    view = _summarise(state) if isinstance(state, dict) else {}
    view["state"] = STATE_UNKNOWN
    view["reason"] = reason
    return view


def cmd_query(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    state, state_error = _read_state_optional(state_path)
    if state is None:
        return {"ok": True, "state": {"state": STATE_UNKNOWN,
                                      "reason": state_error or "还没有 state.json"}}
    try:
        request = _load_request(state_dir)
    except (WorkerError, OSError, ValueError) as exc:
        return {"ok": True, "state": _unknown_view(state, f"request.json 不可核：{exc}")}
    reason = binding_reason(request, state)
    if reason:
        return {"ok": True, "state": _unknown_view(state, f"运行记录与请求不一致：{reason}")}
    current = state.get("state")
    if current in FINAL_STATES:
        if state.get("stop_verified") is not True:
            return {"ok": True,
                    "state": _unknown_view(state, "终态记录缺少「原进程及其进程组已停」的证据")}
        return {"ok": True, "state": _summarise(state)}
    pid, birth = state.get("pid"), state.get("birth")
    if not pid:
        return {"ok": True, "state": _summarise(state),
                "reason": "还没有 pid（启动中或取消已在启动前登记）⇒ 不能宣称已停"}
    if not process_alive(pid):
        return {"ok": True, "state": _summarise(state),
                "reason": "进程已不在，但没有终态记录 ⇒ 不可判（可能 supervisor 被杀）"}
    if birth is not None:
        actual = process_birth(pid)
        if actual is not None and actual != birth:
            return {"ok": True, "state": _summarise(state),
                    "reason": "pid 的出生时间变了（PID 复用）⇒ 原进程已不在、终态不可判"}
    return {"ok": True, "state": _summarise(state)}


def _already_verified_terminal(state_path: Path):
    """并发下 supervisor 可能先写了已核终态：此时取消不该把它改写成 cancelled。"""
    current, _ = _read_state_optional(state_path)
    if isinstance(current, dict) and current.get("state") in FINAL_STATES \
            and current.get("stop_verified") is True:
        return current
    return None


def _cancel_terminal_locked(state_path: Path, state: dict, *, reason: str, method: str,
                            evidence: dict | None = None) -> dict:
    """停止已核验后落终态。**报告写不进去不改变已发生的停止**：照样如实回报。"""
    existing = _already_verified_terminal(state_path)
    if existing is not None:
        return {"ok": True, "state": _summarise(existing), "reason": "已经结束，无需再取消"}
    stop_evidence = dict(evidence or {})
    stop_evidence.setdefault("method", method)
    state.update(state=STATE_CANCELLED, finished_at=time.time(), reason=reason,
                 stop_verified=True, cancel_requested=True,
                 stop_evidence=stop_evidence)
    record_error = None
    try:
        _write_json(state_path, state)
    except OSError as exc:
        record_error = f"{type(exc).__name__}: {exc}"
    result = {"ok": True, "state": _summarise(state)}
    if record_error:
        result.update(recorded=False,
                      warning=f"停止已核验，但运行记录写入失败（报告不改变已发生的停止）：{record_error}")
    return result


def _cancel_unknown_locked(state_path: Path, state: dict | None, reason: str, *,
                           binding: dict | None = None,
                           stop_evidence: dict | None = None) -> dict:
    """没能证明已停：只记 unknown + 取消意向，⛔ 不写 cancelled/failed，保留 pid/birth。

    已有终态记录时**不覆盖**它（哪怕缺停止证据）：保留原记录，只回报 unknown。
    """
    current, _ = _read_state_optional(state_path)
    if isinstance(current, dict) and current.get("state") in FINAL_STATES:
        if current.get("stop_verified") is True:
            return {"ok": True, "state": _summarise(current), "reason": "已经结束，无需再取消"}
        view = _summarise(current)
        view.update(state=STATE_UNKNOWN, reason=reason, stop_verified=False)
        return {"ok": False, "state": view}
    if isinstance(state, dict):
        state.update(state=STATE_UNKNOWN, finished_at=time.time(), reason=reason,
                     stop_verified=False, cancel_requested=True)
        if stop_evidence is not None:
            state["stop_evidence"] = stop_evidence
        record_error = None
        try:
            _write_json(state_path, state)
        except OSError as exc:
            record_error = f"{type(exc).__name__}: {exc}"
        view = _summarise(state)
        view.update(state=STATE_UNKNOWN, reason=reason, stop_verified=False,
                    cancel_requested=True)
        if stop_evidence is not None:
            view["stop_evidence"] = stop_evidence
        result = {"ok": False, "state": view}
        if record_error:
            result["warning"] = f"运行记录写入失败：{record_error}"
        return result
    view = {"state": STATE_UNKNOWN, "reason": reason, "cancel_requested": True,
            "stop_verified": False}
    view.update(binding or {})
    return {"ok": False, "state": view}


def _cancel_refused(state, reason: str) -> dict:
    """token 核不过/记录对不上：⛔ 不写任何状态、不落意向，避免用错 token 改写运行记录。"""
    view = _summarise(state) if isinstance(state, dict) else {}
    view.update(state=STATE_UNKNOWN, reason=reason, stop_verified=False)
    if isinstance(state, dict) and "cancel_requested" in state:
        view["cancel_requested"] = state.get("cancel_requested")
    return {"ok": False, "state": view}


def cmd_cancel(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    request_path = state_dir / "request.json"
    if not state_dir.is_dir() or (not state_path.is_file() and not request_path.is_file()):
        return {"ok": False, "state": {"state": STATE_UNKNOWN,
                                       "reason": "没有运行记录，不能确认取消"}}
    with state_lock(state_dir):
        request = _read_request_optional(state_dir)
        state, state_error = _read_state_optional(state_path)
        expected = None
        for source in (state, request):
            if isinstance(source, dict) and isinstance(source.get("token"), str) \
                    and source["token"]:
                expected = source["token"]
                break
        if expected is None:
            return _cancel_refused(state, "运行记录里没有可核的 token，拒绝猜取消")
        if not isinstance(args.token, str) or args.token != expected:
            return _cancel_refused(state, "取消 token 与运行记录不符")
        if request is not None and state is not None:
            reason = binding_reason(request, state)
            if reason:
                return _cancel_refused(state, f"运行记录与请求不一致：{reason}")
        request_hash = (state or {}).get("request_hash")
        if request_hash is None and request is not None:
            request_hash = request.get("request_hash")
        attempt_id = (state or {}).get("attempt_id")
        if attempt_id is None and request is not None:
            attempt_id = request.get("attempt_id")
        binding = {"request_hash": request_hash, "token": expected,
                   "attempt_id": attempt_id}
        marker_error = None
        try:
            _write_json(cancel_marker_path(state_dir),
                        {"kind": "cancel-requested", "request_hash": request_hash,
                         "token": expected, "attempt_id": attempt_id,
                         "at": time.time(), "protocol": WORKER_VERSION})
        except OSError as exc:
            marker_error = f"{type(exc).__name__}: {exc}"
        if state is None:
            if state_error:
                return _cancel_unknown_locked(
                    state_path, None,
                    state_error + "：不能核对 pid，取消未确认",
                    binding=binding)
            return _cancel_unknown_locked(
                state_path, None,
                "取消意向已登记，但还没有运行记录可核 ⇒ 不宣称已停止",
                binding=binding)
        if state.get("state") in FINAL_STATES:
            if state.get("stop_verified") is True:
                return {"ok": True, "state": _summarise(state),
                        "reason": "已经结束，无需再取消"}
            return _cancel_unknown_locked(state_path, state,
                                          "已有终态记录但缺少「原进程及其进程组已停」的证据")
        state["cancel_requested"] = True
        record_error = None
        try:
            _write_json(state_path, state)
        except OSError as exc:
            record_error = f"{type(exc).__name__}: {exc}"
        pid, birth = state.get("pid"), state.get("birth")
        if not pid:
            result = _cancel_unknown_locked(state_path, state,
                                            "cancel-requested-before-pid")
            result["cancel_requested"] = True
            if marker_error or record_error:
                result["warning"] = (f"取消意向/运行记录写入失败："
                                     f"{marker_error or record_error}")
            return result
        if birth is None:
            # 没有出生时间就无法排除 PID 复用：⛔ 宁可 unknown 也不向可能不是原 job 的组发信号。
            return _cancel_unknown_locked(
                state_path, state,
                "运行记录缺进程出生时间，不能安全排除 PID 复用：拒绝发信号",
                stop_evidence={"pid": pid, "birth": None, "method": "identity-unknown"})
        actual = process_birth(pid)
        if birth is not None and actual is not None and actual != birth:
            # PID 被复用：⛔ 绝不向新进程发信号；只有同组已空才敢说原 job 已经停了。
            # 正在执行取消的这个进程不可能是「已死的原 leader 的子进程」，可以排除。
            members = group_members(pid)
            if members is not None:
                members = members - {os.getpid()}
                gone = not members
            else:
                gone = None
            if gone is True:
                return _cancel_terminal_locked(
                    state_path, state, reason="pid-reused-original-gone",
                    method="pid-reused-group-empty",
                    evidence={"pid": pid, "birth": birth, "actual_birth": actual,
                              "members_after": 0})
            return _cancel_unknown_locked(
                state_path, state,
                "pid 已复用、原进程已不在，但同组仍有活进程或无法证明 ⇒ 不冒充已停",
                stop_evidence={"pid": pid, "birth": birth, "actual_birth": actual,
                               "members_after": None if gone is None else "alive"})
        outcome = _stop_group(pid, birth)
        if outcome["verified"]:
            return _cancel_terminal_locked(state_path, state,
                                           reason=outcome["method"],
                                           method=outcome["method"],
                                           evidence=outcome["evidence"])
        return _cancel_unknown_locked(
            state_path, state,
            "TERM→KILL 后原进程组仍在或本平台不能证明已停，不能宣称已取消",
            stop_evidence=outcome["evidence"])


def cmd_collect(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    state, state_error = _read_state_optional(state_path)
    if state is None:
        raise WorkerError(f"没有可读的运行记录，不能取回：{state_error or '缺 state.json'}",
                          definitive=True)
    request = _load_request(state_dir)
    reason = binding_reason(request, state)
    if reason:
        raise WorkerError(f"运行记录与请求不一致：{reason}", definitive=True)
    if state.get("state") != STATE_COMPLETED:
        return {"ok": False, "state": state.get("state"),
                "reason": "原 job 不是 completed；不能取回（缺件只能是 partial，不是完成）"}
    if state.get("stop_verified") is not True:
        raise WorkerError("completed 记录缺少「原进程及其进程组已停」的证据，拒绝取回",
                          definitive=True)
    spec = state["spec"]
    output_dir = Path(state["output_dir"])
    artifacts, missing = [], []
    for rel in spec["required_artifacts"]:
        if not _safe_rel(rel):
            missing.append(rel)
            continue
        try:
            target = safe_join(output_dir, rel)
        except WorkerError:
            missing.append(rel)
            continue
        if not target.is_file():
            missing.append(rel)
            continue
        data = target.read_bytes()
        artifacts.append({"path": rel, "sha256": sha_bytes(data), "size": len(data)})
    return {"ok": True, "state": STATE_COMPLETED,
            "request_hash": state.get("request_hash"),
            "token": state.get("token"),
            "attempt_id": state.get("attempt_id"),
            "stop_verified": True,
            "output_dir": state["output_dir"],
            "artifacts": artifacts, "missing": missing}


def _apply_final(state_path: Path, updates: dict) -> bool:
    """终态写入的唯一入口。取消已登记 / 已是终态 ⇒ 不覆盖。

    不变量：`completed`/`failed`/`cancelled` 必须带 `stop_verified=true`；否则降级成 `unknown`
    （⛔ 不许让没有停止证明的结果冒充终态）。返回是否真的写进去了。
    """
    try:
        state = _read_json(state_path)
    except (OSError, ValueError):
        return False
    if not isinstance(state, dict):
        return False
    if state.get("state") in FINAL_STATES:
        return False
    updates = dict(updates)
    if updates.get("state") in FINAL_STATES and updates.get("stop_verified") is not True:
        updates.update(state=STATE_UNKNOWN, stop_verified=False)
    state.update(updates)
    try:
        _write_json(state_path, state)
    except OSError:
        return False
    return True


def _finalise(state_path: Path, updates: dict, *, locked: bool = False) -> bool:
    if locked:
        return _apply_final(state_path, updates)
    try:
        with state_lock(Path(state_path).parent):
            return _apply_final(state_path, updates)
    except WorkerError:
        # 报告层拿不到锁不能改变已发生的停止：退回直接写（仍受终态不覆盖保护）。
        return _apply_final(state_path, updates)


def cmd_supervise(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    request = _load_request(state_dir)
    state, state_error = _read_state_optional(state_path)
    if state is None:
        return {"ok": False, "reason": f"没有可读的运行记录：{state_error or '缺 state.json'}"}
    reason = binding_reason(request, state)
    if reason:
        return {"ok": False, "reason": f"运行记录与请求不一致：{reason}"}
    block = _launch_block(state_dir, state)
    if block is not None:
        blocked = _blocked_state(state, block)
        recorded = _finalise(state_path, blocked)
        return {"ok": True, "cancelled": blocked.get("state") == STATE_CANCELLED,
                "recorded": recorded, "reason": blocked.get("reason")}
    spec = state["spec"]
    output_dir = Path(state["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout = open(state_dir / "stdout.log", "ab")
    stderr = open(state_dir / "stderr.log", "ab")
    try:
        env = _child_env(state["environment"])
        argv = state["argv"]
        popen_kwargs = {"cwd": spec["code_root"], "env": env, "stdin": subprocess.DEVNULL,
                        "stdout": stdout, "stderr": stderr, "shell": False, "close_fds": True}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        with state_lock(state_dir):
            state, state_error = _read_state_optional(state_path)
            if state is None:
                return {"ok": False,
                        "reason": f"拉起前运行记录不可读：{state_error or '缺 state.json'}"}
            reason = binding_reason(request, state)
            if reason:
                return {"ok": False, "reason": f"运行记录与请求不一致：{reason}"}
            if state.get("state") == STATE_RUNNING and state.get("pid"):
                return {"ok": True, "skipped": True, "state": _summarise(state)}
            if state.get("state") in FINAL_STATES:
                return {"ok": True, "skipped": True, "state": _summarise(state)}
            block = _launch_block(state_dir, state)
            if block is not None:
                blocked = _blocked_state(state, block)
                _apply_final(state_path, blocked)
                return {"ok": True, "cancelled": blocked.get("state") == STATE_CANCELLED,
                        "recorded": True, "reason": blocked.get("reason")}
            try:
                process = _popen(argv, **popen_kwargs)
            except (OSError, ValueError) as exc:
                _apply_final(state_path,
                             {"state": STATE_FAILED, "finished_at": time.time(),
                              "reason": f"launch_error: {exc}", "stop_verified": True,
                              "stop_evidence": {"method": "never-launched",
                                                "detail": str(exc)}})
                return {"ok": False, "reason": f"launch error: {exc}"}
            state.update(pid=process.pid, birth=process_birth(process.pid),
                         state=STATE_RUNNING, started_at=time.time(), argv=argv)
            _write_json(state_path, state)
    finally:
        stdout.close()
        stderr.close()
    timeout = float(state["timeout_s"])
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        outcome = _stop_group(process.pid, state.get("birth"))
        try:
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            returncode = None
        updates = {"finished_at": time.time(), "timed_out": True, "exitcode": None,
                   "signal": None, "stop_evidence": outcome["evidence"]}
        if outcome["verified"]:
            updates.update(state=STATE_FAILED, stop_verified=True, reason="hard-timeout",
                           signal=(None if returncode is None or returncode >= 0
                                   else -returncode))
        else:
            # 超时确实发生了，但「远端已停」没被证明：⛔ 不许冒充 failed 终态。
            updates.update(state=STATE_UNKNOWN, stop_verified=False,
                           reason="hard-timeout-group-not-proven")
        recorded = _finalise(state_path, updates)
        return {"ok": outcome["verified"], "timed_out": True,
                "stop_verified": outcome["verified"], "recorded": recorded}
    if returncode is None:
        recorded = _finalise(state_path, {"state": STATE_UNKNOWN, "finished_at": time.time(),
                                          "stop_verified": False, "reason": "no-returncode"})
        return {"ok": False, "reason": "no returncode", "recorded": recorded}
    gone = group_gone(process.pid)
    if gone is not True:
        recorded = _finalise(state_path,
                             {"state": STATE_UNKNOWN, "finished_at": time.time(),
                              "exitcode": returncode if returncode >= 0 else None,
                              "signal": None if returncode >= 0 else -returncode,
                              "stop_verified": False,
                              "stop_evidence": {"method": "wait-group-check",
                                                "members_after": (None if gone is None
                                                                  else "alive")},
                              "reason": "main-exit-group-not-verified"})
        return {"ok": False, "reason": "主进程已退出但同组仍有活进程/不能证明已停",
                "stop_verified": False, "recorded": recorded}
    common = {"finished_at": time.time(), "stop_verified": True,
              "stop_evidence": {"method": "wait-group-empty", "pid": process.pid,
                                "birth": state.get("birth"), "members_after": 0}}
    if returncode < 0:
        recorded = _finalise(state_path, {**common, "state": STATE_FAILED, "exitcode": None,
                                          "signal": -returncode,
                                          "reason": f"signal {-returncode}"})
    elif returncode == 0:
        recorded = _finalise(state_path, {**common, "state": STATE_COMPLETED, "exitcode": 0,
                                          "signal": None, "reason": None})
    else:
        recorded = _finalise(state_path, {**common, "state": STATE_FAILED,
                                          "exitcode": returncode, "signal": None,
                                          "reason": f"exit {returncode}"})
    return {"ok": True, "recorded": recorded}


def cmd_version(args) -> dict:
    return {"ok": True, "version": WORKER_VERSION, "protocol": WORKER_VERSION,
            "features": list(PROTOCOL_FEATURES)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ssh_worker", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("request-write", "start", "query", "cancel", "collect", "supervise"):
        item = sub.add_parser(name)
        item.add_argument("--state-dir", required=True)
        if name == "cancel":
            item.add_argument("--token", required=True)
    sub.add_parser("version")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        handler = {
            "request-write": cmd_request_write,
            "start": cmd_start,
            "query": cmd_query,
            "cancel": cmd_cancel,
            "collect": cmd_collect,
            "supervise": cmd_supervise,
            "version": cmd_version,
        }[args.command]
        try:
            result = handler(args)
        except (OSError, ValueError) as exc:
            raise WorkerError(f"{type(exc).__name__}: {exc}") from exc
        print(canonical(result))
        return 0
    except WorkerError as exc:
        print(canonical({"ok": False, "error": str(exc), "definitive": exc.definitive}))
        return 0
    except Exception as exc:  # noqa: BLE001 - 未知错误也要给结构化结果
        print(canonical({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                         "definitive": False}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
