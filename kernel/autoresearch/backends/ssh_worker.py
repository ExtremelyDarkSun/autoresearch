# GENERATED-BY: ssh backend migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：远端 worker（工作单元布局、指纹核验、独立环境、进程组、硬超时、取消/取回）
# PURPOSE: 在远端机器上执行一次 RunSpec：建孤立 code/env/input/output、核代码/题单/环境指纹真实字节、
#          起独立进程组、持久运行记录、硬超时、取消核 token+进程出生时间、按清单取回产物。
# ORIGIN: none（新文件）。对接口径见 kernel/autoresearch/backends/ssh.py；协议来自 runtime/execution.py 的
#         RunSpec / JobStatus / ResultBundle。
# CUMULATIVE-DELTA: 首版。纯标准库；POSIX 用 setsid/killpg，Windows 用 CREATE_NEW_PROCESS_GROUP + taskkill，
#         但真实远端按 POSIX 设计；取消必须核 token 与进程出生时间，防 PID 复用误杀。
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
      .ssh_worker/<request_hash>/   # 运行记录（request.json / state.json / 日志）

运行记录 `state.json` 的字段（原子写）::

    request_hash, attempt_id, state, token, pid, birth, started_at, finished_at,
    timeout_s, argv, spec, output_dir, exitcode, signal, timed_out, reason

安全边界
========

* **进程组**：子进程用新 session（POSIX）或新进程组（Windows）启动；取消对整个组发
  TERM → KILL，然后**重新核**「进程不在或出生时间已变」才写 `cancelled`；
  出生时间不符 ⇒ 原进程已死（PID 被复用），只记 cancelled，**绝不向新进程发信号**。
* **未知不冒终态**：没有终态记录、进程查不到、记录损坏、连接中断 ⇒ `unknown`。
* **指纹核真实字节**：代码按整棵树的 `{相对路径: sha256}` 重算；题单按
  `input/manifest.json` 里逐题文件重算；环境按解释器字节 + 版本 + 包清单重算。
  ⛔ 不认 `isolated_environment=True` 这种布尔声明。
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
from pathlib import Path

WORKER_VERSION = 1
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
    script = ("import json,sys;print(json.dumps({'version':sys.version.split()[0],"
              "'prefix':sys.prefix,'base_prefix':getattr(sys,'base_prefix',sys.prefix),"
              "'executable':sys.executable}))")
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
        "packages": sorted(str(item) for item in (packages or [])),
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


def _wait_gone(pid: int, birth, seconds: float) -> bool:
    deadline = time.time() + max(seconds, 0)
    while time.time() < deadline:
        if not process_alive(pid):
            return True
        current = process_birth(pid)
        if birth is not None and current is not None and current != birth:
            return True
        time.sleep(0.1)
    if not process_alive(pid):
        return True
    current = process_birth(pid)
    return birth is not None and current is not None and current != birth


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
        return {"ok": True, "state_dir": str(state_dir), "request_hash": request["request_hash"],
                "reused": True}
    _write_json(request_path, request)
    return {"ok": True, "state_dir": str(state_dir), "request_hash": request["request_hash"],
            "reused": False}


def _load_request(state_dir: Path) -> dict:
    request_path = state_dir / "request.json"
    if not request_path.is_file():
        raise WorkerError("没有 request.json：这个请求从未登记", definitive=True)
    request = validate_request(_read_json(request_path))
    if request_hash(request) != request["request_hash"]:
        raise WorkerError("request.json 内容被改过", definitive=True)
    return request


def _summarise(state: dict) -> dict:
    keys = ("request_hash", "attempt_id", "state", "pid", "birth", "started_at",
            "finished_at", "exitcode", "signal", "timed_out", "token", "reason",
            "output_dir", "timeout_s", "argv")
    return {key: state.get(key) for key in keys}


def cmd_start(args) -> dict:
    state_dir = Path(args.state_dir)
    request = _load_request(state_dir)
    state_path = state_dir / "state.json"
    if state_path.is_file():
        state = _read_json(state_path)
        if state.get("request_hash") != request["request_hash"]:
            raise WorkerError("state.json 与 request.json 不是同一次请求", definitive=True)
        if request.get("token") and state.get("token") != request["token"]:
            raise WorkerError("state.json 的 token 与本次请求不符", definitive=True)
        if state.get("state") in KNOWN_STATES:
            return {"ok": True, "state": _summarise(state), "reused": True}
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
    }
    _write_json(state_path, state)
    supervisor_log = state_dir / "supervisor.log"
    with open(supervisor_log, "ab") as log:
        popen_kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
                        "close_fds": True, "shell": False}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                          "supervise", "--state-dir", str(state_dir)], **popen_kwargs)
    deadline = time.time() + START_WAIT_SECONDS
    while time.time() < deadline:
        current = _read_json(state_path) if state_path.is_file() else state
        if current.get("state") in KNOWN_STATES and current.get("state") != STATE_STARTING:
            return {"ok": True, "state": _summarise(current), "reused": False}
        if current.get("state") == STATE_STARTING and current.get("pid"):
            return {"ok": True, "state": _summarise(current), "reused": False}
        time.sleep(0.1)
    return {"ok": True, "state": _summarise(_read_json(state_path)), "reused": False,
            "warning": "supervisor 未在等待窗口内报告 pid；按不可判处理，后续用 query 复核"}


def cmd_query(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    if not state_path.is_file():
        return {"ok": True, "state": {"state": STATE_UNKNOWN, "reason": "还没有 state.json"}}
    try:
        state = _read_json(state_path)
    except (OSError, ValueError) as exc:
        return {"ok": True, "state": {"state": STATE_UNKNOWN,
                                      "reason": f"state.json 不可读：{exc}"}}
    current = state.get("state")
    if current in FINAL_STATES:
        return {"ok": True, "state": _summarise(state)}
    pid, birth = state.get("pid"), state.get("birth")
    if not pid:
        return {"ok": True, "state": _summarise(state),
                "reason": "还没有 pid（启动中或 supervisor 未登记）"}
    if not process_alive(pid):
        return {"ok": True, "state": _summarise(state),
                "reason": "进程已不在，但没有终态记录 ⇒ 不可判（可能 supervisor 被杀）"}
    if birth is not None:
        actual = process_birth(pid)
        if actual is not None and actual != birth:
            return {"ok": True, "state": _summarise(state),
                    "reason": "pid 的出生时间变了（PID 复用）⇒ 原进程已不在、终态不可判"}
    return {"ok": True, "state": _summarise(state)}


def cmd_cancel(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    if not state_path.is_file():
        return {"ok": False, "state": {"state": STATE_UNKNOWN, "reason": "没有运行记录，不能确认取消"}}
    state = _read_json(state_path)
    if args.token != state.get("token"):
        return {"ok": False, "state": {"state": STATE_UNKNOWN,
                                       "reason": "取消 token 与运行记录不符"}}
    if state.get("state") in FINAL_STATES:
        return {"ok": True, "state": _summarise(state), "reason": "已经结束，无需再取消"}
    pid, birth = state.get("pid"), state.get("birth")
    if not pid:
        # 还没起进程：直接记 cancelled（没有可杀的完整进程组）。
        state.update(state=STATE_CANCELLED, finished_at=time.time(),
                     reason="cancel-before-pid")
        _write_json(state_path, state)
        return {"ok": True, "state": _summarise(state)}
    if not process_alive(pid):
        state.update(state=STATE_CANCELLED, finished_at=time.time(),
                     reason="process-already-gone")
        _write_json(state_path, state)
        return {"ok": True, "state": _summarise(state)}
    actual = process_birth(pid)
    if birth is not None and actual is not None and actual != birth:
        # PID 被复用：原进程已经死了，⛔ 绝不能向新进程发信号。
        state.update(state=STATE_CANCELLED, finished_at=time.time(),
                     reason="pid-reused-original-gone")
        _write_json(state_path, state)
        return {"ok": True, "state": _summarise(state)}
    _term_group(pid)
    if _wait_gone(pid, birth, GRACE_SECONDS):
        state.update(state=STATE_CANCELLED, finished_at=time.time(), reason="term")
        _write_json(state_path, state)
        return {"ok": True, "state": _summarise(state)}
    _kill_group(pid)
    if _wait_gone(pid, birth, GRACE_SECONDS):
        state.update(state=STATE_CANCELLED, finished_at=time.time(), reason="kill")
        _write_json(state_path, state)
        return {"ok": True, "state": _summarise(state)}
    return {"ok": False, "state": {"state": STATE_UNKNOWN,
                                   "reason": "TERM→KILL 后进程/进程组仍在，不能宣称已取消"}}


def cmd_collect(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    if not state_path.is_file():
        raise WorkerError("没有运行记录，不能取回", definitive=True)
    state = _read_json(state_path)
    if state.get("state") != STATE_COMPLETED:
        return {"ok": False, "state": state.get("state"),
                "reason": "原 job 不是 completed；不能取回（缺件只能是 partial，不是完成）"}
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
    return {"ok": True, "state": STATE_COMPLETED, "output_dir": state["output_dir"],
            "artifacts": artifacts, "missing": missing}


def _finalise(state_path: Path, updates: dict) -> None:
    try:
        state = _read_json(state_path)
    except (OSError, ValueError):
        return
    if state.get("state") == STATE_CANCELLED:
        return  # 取消已登记：⛔ 不覆盖成别的终态
    state.update(updates)
    _write_json(state_path, state)


def cmd_supervise(args) -> dict:
    state_dir = Path(args.state_dir)
    state_path = state_dir / "state.json"
    state = _read_json(state_path)
    request = _load_request(state_dir)
    spec = state["spec"]
    output_dir = Path(state["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout = open(state_dir / "stdout.log", "ab")
    stderr = open(state_dir / "stderr.log", "ab")
    env = _child_env(state["environment"])
    argv = state["argv"]
    popen_kwargs = {"cwd": spec["code_root"], "env": env, "stdin": subprocess.DEVNULL,
                    "stdout": stdout, "stderr": stderr, "shell": False, "close_fds": True}
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(argv, **popen_kwargs)
    except (OSError, ValueError) as exc:
        _finalise(state_path, {"state": STATE_FAILED, "finished_at": time.time(),
                               "reason": f"launch_error: {exc}"})
        return {"ok": False, "reason": "launch error"}
    state.update(pid=process.pid, birth=process_birth(process.pid),
                 state=STATE_RUNNING, started_at=time.time(),
                 argv=argv, environment=state["environment"])
    _write_json(state_path, state)
    timeout = float(state["timeout_s"])
    try:
        returncode = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _term_group(process.pid)
        if not _wait_gone(process.pid, state.get("birth"), GRACE_SECONDS):
            _kill_group(process.pid)
            _wait_gone(process.pid, state.get("birth"), GRACE_SECONDS)
        try:
            returncode = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            returncode = None
        _finalise(state_path, {"state": STATE_FAILED, "finished_at": time.time(),
                               "exitcode": returncode if returncode is not None and returncode >= 0 else None,
                               "signal": "SIGTERM", "timed_out": True,
                               "reason": "hard-timeout"})
        return {"ok": True, "timed_out": True}
    if returncode is None:
        _finalise(state_path, {"state": STATE_UNKNOWN, "finished_at": time.time(),
                               "reason": "no-returncode"})
        return {"ok": False, "reason": "no returncode"}
    if returncode < 0:
        _finalise(state_path, {"state": STATE_FAILED, "finished_at": time.time(),
                               "exitcode": None, "signal": -returncode,
                               "reason": f"signal {-returncode}"})
    elif returncode == 0:
        _finalise(state_path, {"state": STATE_COMPLETED, "finished_at": time.time(),
                               "exitcode": 0, "signal": None, "reason": None})
    else:
        _finalise(state_path, {"state": STATE_FAILED, "finished_at": time.time(),
                               "exitcode": returncode, "signal": None,
                               "reason": f"exit {returncode}"})
    return {"ok": True}


def cmd_version(args) -> dict:
    return {"ok": True, "version": WORKER_VERSION}


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
