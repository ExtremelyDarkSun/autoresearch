# GENERATED-BY: ssh backend migration, 2026-11-09
# LAST-EDIT: 2026-11-09 首版：系统 ssh/scp Adapter（submit/query/collect/cancel）+ 显式 provision + 本地/远端 receipt
# PURPOSE: 把 runtime.execution.Adapter 接到一台通过系统 ssh 可达的 POSIX 机器：默认关闭、不读凭据、不连真机；
#          远端执行交给随 provision 推送的 ssh_worker.py，本文件只做安全的命令构造、状态映射与产物回传。
# ORIGIN: none（新文件）。协议来自 runtime/execution.py 的 RunSpec / JobStatus / ResultBundle / Adapter。
# CUMULATIVE-DELTA: 首版。三条硬边界：① `SSHDeployment.enabled` 默认 False，任何操作先抛 PermissionError；
#          ② 所有远端命令都用**单一字符串**经 `shlex.quote` 逐 token 构造，⛔ 不拼未 quote 的参数、⛔ 不用 shell=True；
#          ③ 连接失败/超时/远端不可判一律 `JobStatus(..., 'unknown')`，⛔ 不冒 completed/failed。
"""SSH 后端（系统 ssh/scp；默认关闭）。

用法
====

    deployment = SSHDeployment(enabled=True, host="gpu-box", user="runner",
                               known_hosts="~/.ssh/known_hosts",
                               remote_dir="/home/runner/autoresearch")
    adapter = SSHAdapter(deployment)
    adapter.provision()                 # **显式**推送 ssh_worker.py（不改远端别的东西）
    execution = Execution(store, adapter)

`enabled=False`（默认）时任何 Adapter 方法都抛 `PermissionError`，一个连接都不发。

远端目录（worker 自己建的）::

    <unit_root>/.ssh_worker/<request_hash32>/
        request.json     # 本次请求（含 spec / argv / timeout / environment）
        state.json       # 运行记录（token / pid / birth / state / exitcode / signal）
        stdout.log, stderr.log, supervisor.log

`state_dir` 由 request_hash 派生（纯十六进制），因此不会把 request_key/题号/argv 里的
shell 元字符带进 scp/ssh 路径。

安全口径
========

* **不连真机、不读凭据**：只把 `key_file` 原样交给系统 ssh；本文件不打开它。
* **单命令**：远端执行形如 `python3 <worker> <subcommand> --state-dir <dir>`，
  由 `shlex.quote` 逐 token 拼成一个字符串交给 ssh；scp 的远端路径只来自
  `remote_dir` 与十六进制 hash（`remote_dir` 本身也做白名单校验）。
* **幂等**：本地 receipt 用 `O_CREAT|O_EXCL` 原子创建；远端 receipt 由 worker 用
  `mkdir` 原子创建。同一 request_key 的第二次 submit 只 query，不会重发；内容变了直接拒。
* **未知不冒终态**：worker 只有在有终态记录（或取消后重新核过进程组已无进程）时才报终态；
  连接中断、SSH 超时、字段缺失 ⇒ 本层一律 `unknown`，并且下次 submit/query 也不会重提。
* **产物**：只按 `spec.required_artifacts` 取；缺失的不补、不造假，交给
  `Execution.validate_results` 判 partial；scp 回来的每个字节都再核一次 SHA256。
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import secrets
import shlex
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..runtime.execution import (DefinitiveFailure, JobStatus, ResultBundle, RunSpec,
                                 canonical, digest)

WORKER_VERSION = 1
DEFAULT_TIMEOUT_S = 3600.0
_SAFE_HOST = re.compile(r"^[A-Za-z0-9_.-]+$")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./-]+$")
_SAFE_REL = re.compile(r"^[A-Za-z0-9_./-]+$")


# ---------------------------------------------------------------------------
# 部署配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SSHDeployment:
    """一台 SSH 机器的部署参数。**默认 enabled=False，不会连任何机器。**"""

    enabled: bool = False
    host: str | None = None
    user: str | None = None
    port: int = 22
    key_file: str | None = None
    known_hosts: str | None = None
    remote_dir: str = "/tmp/autoresearch-ssh"
    python: str = "python3"
    state_root: str | None = None
    connect_timeout: float = 15.0
    command_timeout: float = 120.0
    start_timeout: float = 60.0
    cancel_grace: float = 10.0

    def require_enabled(self) -> None:
        if self.enabled is not True:
            raise PermissionError(
                "SSH 后端默认关闭：不会连接任何机器。需要显式 SSHDeployment(enabled=True, …)")
        if not isinstance(self.host, str) or not _SAFE_HOST.match(self.host):
            raise PermissionError("enabled=True 时必须给合法 host（字母/数字/._-）")
        if not isinstance(self.user, str) or not _SAFE_HOST.match(self.user):
            raise PermissionError("enabled=True 时必须给合法 user（字母/数字/._-）")
        if not isinstance(self.known_hosts, str) or not self.known_hosts.strip():
            raise PermissionError("必须显式给 known_hosts：本层不做 TOFU，也不关主机密钥校验")
        if type(self.port) is not int or not (0 < self.port < 65536):
            raise PermissionError("port 必须是 1–65535 的整数")
        if not isinstance(self.remote_dir, str) or not _SAFE_PATH.match(self.remote_dir) \
                or ".." in self.remote_dir.split("/"):
            raise PermissionError("remote_dir 必须是安全的 POSIX 绝对路径（字母/数字/_./-）")
        if not isinstance(self.python, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", self.python):
            raise PermissionError("python 必须是远端解释器命令（不许含空白或 shell 元字符）")
        for value, name, low in ((self.connect_timeout, "connect_timeout", 0),
                                 (self.command_timeout, "command_timeout", 0),
                                 (self.start_timeout, "start_timeout", 0),
                                 (self.cancel_grace, "cancel_grace", 0)):
            if type(value) not in (int, float) or not (low < float(value) <= 3600):
                raise PermissionError(f"{name} 必须是 (0, 3600] 的有限数")

    def identity(self) -> dict:
        return {"host": self.host, "user": self.user, "port": self.port,
                "remote_dir": self.remote_dir, "python": self.python,
                "known_hosts": self.known_hosts, "key_file": self.key_file}

    def ssh_argv(self, command: str) -> list[str]:
        """系统 ssh 的 argv。**一个字符串命令**，由调用方逐 token quote 后拼好。"""
        options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", f"UserKnownHostsFile={self.known_hosts}",
                   "-o", f"ConnectTimeout={max(1, int(self.connect_timeout))}",
                   "-p", str(self.port)]
        if self.key_file:
            options += ["-i", self.key_file]
        return ["ssh", *options, f"{self.user}@{self.host}", command]

    def scp_base(self) -> list[str]:
        options = ["-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", f"UserKnownHostsFile={self.known_hosts}",
                   "-o", f"ConnectTimeout={max(1, int(self.connect_timeout))}",
                   "-P", str(self.port)]
        if self.key_file:
            options += ["-i", self.key_file]
        return ["scp", *options]

    def worker_path(self) -> str:
        return posixpath.join(self.remote_dir, "ssh_worker.py")


def remote_command(tokens) -> str:
    """把一组 token 拼成**单条**远端命令：每个 token 都 `shlex.quote`。

    ⛔ 调用方不得把未经处理的外部字符串直接拼进这里；所有路径/参数都必须是
    本层构造的安全值（hash / 白名单路径）或已 quote 的 token。
    """
    if not tokens:
        raise ValueError("远端命令不能为空")
    return " ".join(shlex.quote(str(token)) for token in tokens)


# ---------------------------------------------------------------------------
# 传输层
# ---------------------------------------------------------------------------


class SystemTransport:
    """真实的系统 ssh/scp。所有子进程都用 argv 列表、`shell=False`。"""

    def __init__(self, deployment: SSHDeployment):
        self.deployment = deployment

    def run(self, argv, *, stdin_bytes=None, timeout=None) -> dict:
        kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                  "timeout": timeout, "shell": False}
        if stdin_bytes is None:
            kwargs["stdin"] = subprocess.DEVNULL
        else:
            kwargs["input"] = stdin_bytes
        try:
            result = subprocess.run(list(argv), **kwargs)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"ssh 命令超时（{timeout}s）：{exc}") from exc
        return {"code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

    def copy_to(self, local_path: str, remote_path: str, *, timeout=None) -> None:
        argv = [*self.deployment.scp_base(), str(local_path),
                f"{self.deployment.user}@{self.deployment.host}:{remote_path}"]
        self._scp(argv, timeout)

    def copy_from(self, remote_path: str, local_path: str, *, timeout=None) -> None:
        argv = [*self.deployment.scp_base(),
                f"{self.deployment.user}@{self.deployment.host}:{remote_path}",
                str(local_path)]
        self._scp(argv, timeout)

    def _scp(self, argv, timeout) -> None:
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"scp 超时（{timeout}s）：{exc}") from exc
        if result.returncode != 0:
            raise OSError(f"scp 失败（{result.returncode}）："
                          f"{result.stderr.decode('utf-8', 'replace')[-300:]}")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SSHAdapter:
    """`runtime.execution.Adapter` 的 SSH 实现。"""

    worker_local_path = Path(__file__).with_name("ssh_worker.py")

    def __init__(self, deployment: SSHDeployment, *, transport=None, clock=None):
        import time as _time
        self.deployment = deployment
        self.transport = transport or SystemTransport(deployment)
        self.clock = clock or _time.time
        self._provisioned = False

    # -- 基础 ---------------------------------------------------------------

    def _require(self) -> None:
        self.deployment.require_enabled()
        if not self._provisioned:
            raise PermissionError(
                "远端 worker 还没有 provision：先显式调用 adapter.provision() 推送 ssh_worker.py"
            )

    @property
    def state_root(self) -> Path:
        if self.deployment.state_root:
            return Path(self.deployment.state_root).expanduser()
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                      f"{self.deployment.user}@{self.deployment.host}_{self.deployment.port}")
        return Path.home() / ".autoresearch" / "ssh" / slug

    def _request_hash(self, document: dict) -> str:
        payload = {key: document.get(key) for key in
                   ("spec", "argv", "timeout_s", "environment", "python")}
        return digest(canonical(payload).encode("utf-8"))

    def _state_dir(self, spec: RunSpec, request_digest: str) -> str:
        return posixpath.join(spec.unit_root, ".ssh_worker", request_digest[:32])

    def _receipt_path(self, request_key: str) -> Path:
        return self.state_root / "receipts" / (digest(request_key.encode("utf-8"))[:32] + ".json")

    def _read_receipt(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _write_receipt(self, path: Path, document: dict, *, exclusive: bool) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_WRONLY | (os.O_EXCL if exclusive else os.O_TRUNC)
        try:
            handle = os.open(str(path), flags, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(canonical(document) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return True

    def _ssh_command(self, tokens) -> list[str]:
        return self.deployment.ssh_argv(remote_command(tokens))

    def _worker_tokens(self, subcommand: str, state_dir: str, extra=None) -> list[str]:
        tokens = [self.deployment.python, self.deployment.worker_path(), subcommand,
                  "--state-dir", state_dir]
        if extra:
            tokens += list(extra)
        return tokens

    def _call(self, subcommand: str, state_dir: str, *, stdin_bytes=None,
              extra=None, timeout=None) -> dict:
        if subcommand == "version":
            tokens = [self.deployment.python, self.deployment.worker_path(), "version"]
        else:
            tokens = self._worker_tokens(subcommand, state_dir, extra)
        reply = self.transport.run(self._ssh_command(tokens), stdin_bytes=stdin_bytes,
                                   timeout=timeout or self.deployment.command_timeout)
        return self._parse(reply)

    @staticmethod
    def _parse(reply: dict) -> dict:
        if not isinstance(reply, dict):
            raise RuntimeError("传输层必须返回 {code, stdout, stderr}")
        stdout = reply.get("stdout") or b""
        stderr = reply.get("stderr") or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8")
        lines = [line for line in stdout.decode("utf-8", "replace").splitlines() if line.strip()]
        if not lines:
            raise RuntimeError(f"远端没有输出（ssh 退出码 {reply.get('code')}）："
                               f"{stderr.decode('utf-8', 'replace')[-300:]}")
        try:
            document = json.loads(lines[-1])
        except ValueError as exc:
            raise RuntimeError(f"远端输出不是 JSON：{lines[-1][:300]}") from exc
        if not isinstance(document, dict):
            raise RuntimeError("远端输出必须是 JSON 对象")
        document.setdefault("_ssh_code", reply.get("code"))
        document.setdefault("_stderr", stderr.decode("utf-8", "replace")[-300:])
        return document

    @staticmethod
    def _worker_error(document: dict) -> Exception:
        message = f"远端 worker 拒绝：{document.get('error')}"
        if document.get("definitive") is True:
            return DefinitiveFailure(message)
        return RuntimeError(message)

    # -- provision ----------------------------------------------------------

    def provision(self, *, force: bool = False) -> dict:
        """显式推送 `ssh_worker.py`（scp 到临时名，再用固定 mv 原子替换）。"""
        self.deployment.require_enabled()
        local = self.worker_local_path
        if not local.is_file():
            raise FileNotFoundError(f"本地 worker 不存在：{local}")
        worker_sha = digest(local.read_bytes())
        receipt_path = self.state_root / "provision.json"
        if not force:
            previous = self._read_receipt(receipt_path)
            if previous and previous.get("worker_sha256") == worker_sha \
                    and previous.get("deployment") == self.deployment.identity():
                self._provisioned = True
                return previous
        remote_tmp = self.deployment.worker_path() + ".tmp-" + secrets.token_hex(6)
        self.transport.copy_to(str(local), remote_tmp, timeout=self.deployment.command_timeout)
        moved = self.transport.run(
            self._ssh_command(["mv", remote_tmp, self.deployment.worker_path()]),
            timeout=self.deployment.command_timeout)
        if moved.get("code") != 0:
            raise OSError("远端安装 worker 失败："
                          + (moved.get("stderr") or b"").decode("utf-8", "replace")[-300:])
        version = self._call("version", self.deployment.remote_dir)
        if version.get("ok") is not True or version.get("version") != WORKER_VERSION:
            raise DefinitiveFailure(f"远端 worker 版本不符（本地 {WORKER_VERSION}）：{version}")
        document = {"worker_sha256": worker_sha, "worker_path": self.deployment.worker_path(),
                    "worker_version": WORKER_VERSION, "deployment": self.deployment.identity(),
                    "at": self.clock()}
        self._write_receipt(receipt_path, document, exclusive=False)
        self._provisioned = True
        return document

    # -- 请求构造 -----------------------------------------------------------

    def _request_document(self, spec: RunSpec, attempt_id: str, output_dir: str,
                          token: str) -> dict:
        try:
            spec.validate(require_execution=True)
        except ValueError as exc:
            raise DefinitiveFailure(f"RunSpec 不合规：{exc}") from exc
        config = spec.execution_config or {}
        argv = config.get("argv")
        if not isinstance(argv, list) or not argv \
                or any(not isinstance(item, str) or not item for item in argv):
            raise DefinitiveFailure("execution_config.argv 必须是非空字符串数组（显式 argv）")
        timeout = config.get('timeout_s')
        if type(timeout) not in (int, float) or not (0 < float(timeout) <= 7 * 24 * 3600):
            raise DefinitiveFailure("execution_config.timeout_s 必须是 (0, 7天] 的有限数")
        environment = config.get("environment")
        if environment is None and config.get("python") is not None:
            environment = {"kind": "existing", "python": config.get("python"),
                           "packages": config.get("packages") or []}
        if environment is not None:
            if not isinstance(environment, dict) or environment.get("kind") not in ("venv", "existing"):
                raise DefinitiveFailure("execution_config.environment 必须是 {'kind': venv|existing, …}")
            if environment.get("kind") == "existing":
                python = environment.get("python")
                if not isinstance(python, str) or not python.startswith("/") \
                        or posixpath.normpath(python) != python:
                    raise DefinitiveFailure("existing 环境的 python 必须是规范化 POSIX 绝对路径")
        document = {
            "spec": asdict(spec),
            "argv": list(argv),
            "timeout_s": float(timeout),
            "environment": environment,
            "python": config.get("python"),
            "attempt_id": attempt_id,
            "output_dir": output_dir,
            "token": token,
        }
        document["request_hash"] = self._request_hash(document)
        return document

    # -- Adapter 协议 -------------------------------------------------------

    def submit(self, spec: RunSpec, attempt_id: str, output_dir: str) -> JobStatus:
        self._require()
        token = secrets.token_hex(24)
        document = self._request_document(spec, attempt_id, output_dir, token)
        request_digest = document["request_hash"]
        remote_id = "ssh-" + request_digest[:24]
        state_dir = self._state_dir(spec, request_digest)
        receipt_path = self._receipt_path(spec.request_key)
        created = self._write_receipt(receipt_path, {
            "request_key": spec.request_key,
            "request_hash": request_digest,
            "remote_id": remote_id,
            "state_dir": state_dir,
            "token": token,
            "attempt_id": attempt_id,
            "created_at": self.clock(),
        }, exclusive=True)
        receipt = self._read_receipt(receipt_path)
        if not created:
            if not isinstance(receipt, dict) or receipt.get("request_hash") != request_digest:
                raise DefinitiveFailure(
                    "这个 request_key 已经登记过不同的请求内容：拒绝重发（新请求请换 request_key）"
                )
            # 已经提交过：只 query，⛔ 绝不重提未知 job。
            return self.query(spec.request_key, receipt.get("remote_id", remote_id))
        try:
            written = self._call("request-write", state_dir,
                                 stdin_bytes=canonical(document).encode("utf-8"))
            if written.get("ok") is not True:
                raise self._worker_error(written)
            reply = self._call("start", state_dir, timeout=self.deployment.start_timeout)
            if reply.get("ok") is not True:
                raise self._worker_error(reply)
            state = reply.get("state") or {}
            receipt.update({"state": state.get("state"), "pid": state.get("pid"),
                            "birth": state.get("birth")})
            self._write_receipt(receipt_path, receipt, exclusive=False)
            return self._job_status(state, remote_id)
        except DefinitiveFailure:
            raise
        except Exception as exc:  # noqa: BLE001 - 连断/超时/不可判都只能是 unknown
            return JobStatus(remote_id, "unknown",
                             {"error": f"{type(exc).__name__}: {exc}",
                              "reason": "submit 响应不可判，禁止重提"})

    def query(self, request_key: str, remote_id: str | None) -> JobStatus:
        self._require()
        receipt = self._read_receipt(self._receipt_path(request_key))
        if not isinstance(receipt, dict):
            return JobStatus(remote_id, "unknown", {"reason": "没有本地 receipt，不能确认远端 job"})
        if remote_id and receipt.get("remote_id") and remote_id != receipt["remote_id"]:
            raise ValueError("query 不允许切换原 job 的 remote_id")
        try:
            reply = self._call("query", receipt["state_dir"])
            if reply.get("ok") is not True:
                return JobStatus(remote_id, "unknown", {"reason": reply.get("error")})
            return self._job_status(reply.get("state") or {}, remote_id or receipt.get("remote_id"))
        except Exception as exc:  # noqa: BLE001
            return JobStatus(remote_id, "unknown",
                             {"error": f"{type(exc).__name__}: {exc}",
                              "reason": "query 不可判"})

    def collect(self, request_key: str, remote_id: str, output_dir: str) -> ResultBundle:
        self._require()
        receipt = self._read_receipt(self._receipt_path(request_key))
        if not isinstance(receipt, dict):
            raise RuntimeError("没有本地 receipt，不能取回")
        reply = self._call("collect", receipt["state_dir"])
        if reply.get("ok") is not True:
            raise RuntimeError(f"远端拒绝取回：{reply.get('reason') or reply.get('error')}")
        artifacts: dict[str, bytes] = {}
        manifest: dict[str, str] = {}
        remote_output = reply.get("output_dir")
        if not isinstance(remote_output, str) or not remote_output:
            raise RuntimeError("远端 collect 没有给出合法 output_dir")
        with tempfile.TemporaryDirectory(prefix="autoresearch-ssh-collect-") as directory:
            for item in reply.get("artifacts") or []:
                if not isinstance(item, dict):
                    raise RuntimeError("远端 artifacts 项形状不对")
                rel = item.get("path")
                expected = item.get("sha256")
                if not isinstance(rel, str) or not self._safe_relative(rel) \
                        or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise RuntimeError(f"远端产物清单含非法条目：{item!r}")
                remote_path = remote_output.rstrip("/") + "/" + rel
                local_path = Path(directory) / Path(*rel.split("/"))
                local_path.parent.mkdir(parents=True, exist_ok=True)
                self.transport.copy_from(remote_path, str(local_path),
                                         timeout=self.deployment.command_timeout)
                data = local_path.read_bytes()
                if digest(data) != expected:
                    raise RuntimeError(f"取回后字节与远端 SHA256 不符：{rel}")
                artifacts[rel] = data
                manifest[rel] = expected
        return ResultBundle(artifacts, manifest)

    def cancel(self, request_key: str, remote_id: str | None) -> JobStatus:
        self._require()
        receipt = self._read_receipt(self._receipt_path(request_key))
        if not isinstance(receipt, dict):
            return JobStatus(remote_id, "unknown", {"reason": "没有本地 receipt，不能确认远端 job"})
        token = receipt.get("token")
        if not isinstance(token, str) or not token:
            return JobStatus(remote_id, "unknown",
                             {"reason": "本地没有 token：不能核远端运行记录，拒绝猜取消"})
        try:
            reply = self._call("cancel", receipt["state_dir"], extra=["--token", token])
            if reply.get("ok") is not True:
                return JobStatus(remote_id, "unknown",
                                 {"reason": reply.get("reason") or reply.get("error")})
            return self._job_status(reply.get("state") or {}, remote_id)
        except Exception as exc:  # noqa: BLE001
            return JobStatus(remote_id, "unknown",
                             {"error": f"{type(exc).__name__}: {exc}",
                              "reason": "cancel 不可判"})

    # -- 映射 ---------------------------------------------------------------

    @staticmethod
    def _safe_relative(value: str) -> bool:
        return bool(_SAFE_REL.fullmatch(value)) and "\\" not in value \
            and posixpath.normpath(value) == value \
            and not value.startswith(("/", "../")) and value not in {".", ".."}

    @staticmethod
    def _job_status(state: dict, remote_id: str | None) -> JobStatus:
        if not isinstance(state, dict):
            return JobStatus(remote_id, "unknown", {"reason": "远端状态不是对象"})
        value = state.get("state")
        evidence = {key: state.get(key) for key in
                    ("state", "pid", "birth", "exitcode", "signal", "timed_out", "reason",
                     "started_at", "finished_at")}
        if value in ("completed", "failed", "cancelled"):
            return JobStatus(remote_id, value, evidence)
        if value in ("starting", "running"):
            return JobStatus(remote_id, "running", evidence)
        return JobStatus(remote_id, "unknown", evidence)
