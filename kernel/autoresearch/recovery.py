# GENERATED-BY: independent recovery tool, 2026-11-09
# LAST-EDIT: 2026-11-09
# PURPOSE: Export, verify and restore a private byte/worktree snapshot without importing old runtime state.
# ORIGIN: existing project_layout/store/test conventions; no private content.
# CUMULATIVE-DELTA: First version. Files are selected from the current filesystem (not Git HEAD), SQLite is
#                  copied with sqlite3.backup, credentials are hard-excluded, and restore is verified before write.

r"""Private workspace snapshot export/verify/restore.

The tool deliberately does **not** migrate an old database into the public kernel schema.  It
captures the current filesystem bytes, including a consistent SQLite backup, so a new machine can
run the original implementation.  Live sessions, leases and scheduled tasks remain ``unknown``
until they are actually rebound on the new host.

Safety boundaries:

* credentials, DSH-global state, data, submissions, archives, temp, venvs and node_modules are
  excluded by default; a credential path can never be re-enabled with ``include``;
* symlinks are never followed or stored;
* archive members are checked for traversal, duplicates and symlink modes before restore;
* restore verifies every member/hash before creating a staging tree, then renames it into place;
* Git metadata is not included: worktrees, branches and the original Git flow must be rebuilt.

On Windows, restore internally applies ``\\?\``/``\\?\UNC\`` extended-length paths for file,
directory, chmod, rename and cleanup operations.  Manifest members stay relative and receipts stay
normal paths; callers do not need to type the prefix.

Consistency is deliberately file-by-file, not a point-in-time transaction across the tree.  Each
non-SQLite file is stat-checked before and after its own read; each SQLite database is independently
consistent because it is copied with ``sqlite3.backup``.  A file changed after its read is not rolled
back, and SQLite sidecars are never independent restore units.
"""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import platform
import sqlite3
import stat
import subprocess
import tempfile
import time
import zipfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

FORMAT = "autoresearch-private-recovery"
VERSION = 1
MANIFEST_NAME = "__autoresearch_recovery_manifest__.json"
DEFAULT_ARCHIVE_DIR = ".local/recovery"
GIT_BUNDLE_PREFIX = "__autoresearch_recovery_git__/"
CHUNK = 1024 * 1024

# Directories that are excluded unless explicitly included with ``include``.
SOFT_DIR_NAMES = frozenset({
    "data", "submission", "submissions", "archive", "archives",
    "temp", "tmp", ".local", "venv", ".venv", "venvs", "virtualenv", "node_modules",
    "site-packages", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", "dist", "build", "coverage", ".cache",
})
SOFT_FILE_NAMES = frozenset({".DS_Store", "Thumbs.db"})
SOFT_FILE_SUFFIXES = (".pyc", ".pyo")

# These are never independent files in a restore: the paired database already contains the
# WAL/journal state via sqlite3.backup.  Keep them hard-excluded so ``include`` cannot smuggle
# an old sidecar over the consistent backup.
SQLITE_SIDECAR_SUFFIXES = tuple(
    f"{extension}{suffix}"
    for extension in (".sqlite", ".sqlite3", ".db")
    for suffix in ("-wal", "-shm", "-journal")
)

# Credentials and host-global material are hard-excluded: ``include`` cannot override them.
HARD_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn", ".kaggle_auth", ".ssh", ".dsh", ".aws", ".azure", ".gnupg",
    ".kube", "credentials",
})
HARD_FILE_NAMES = frozenset({
    ".env", ".envrc", ".netrc", "_netrc", ".git-credentials", "credentials.json", "secrets.json",
    "auth.json", ".npmrc", ".pypirc",
})
HARD_FILE_PREFIXES = ("id_rsa", "id_dsa", "id_ecdsa", "id_ed25519")
HARD_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".ppk", ".jks", ".keystore")
ENV_EXAMPLE_NAMES = frozenset({".env.example", ".env.sample", ".env.template"})

# Metadata keys that are safe to describe without exposing a credential value.
REBIND_ITEMS = (
    {"kind": "dsh_profile", "what": "宿主 profile、插件挂载与版本", "action": "按新机重装并核 apply 回执"},
    {"kind": "scheduled_task", "what": "每分钟采集/事件任务", "action": "按新路径重建并核实际回执"},
    {"kind": "session_binding", "what": "旧 leader 会话、targets 与入向目标", "action": "只用新会话重绑，不沿用旧 ID"},
    {"kind": "git_worktrees", "what": "源码 .git、worktree 引用与分支", "action": "重新 clone/init 并重建 worktree；快照不带 Git 元数据"},
    {"kind": "sqlite_runtime", "what": "未完成 run、租约、审批与推进状态", "action": "保持 unknown，逐项核后再推进"},
    {"kind": "credentials", "what": "Kaggle/AutoDL/Feishu/SSH 凭据", "action": "由新机环境或显式文件重新提供"},
)


class RecoveryError(ValueError):
    """A snapshot is unsafe, incomplete or cannot be restored without overwriting."""


# ---------------------------------------------------------------------------
# Path rules
# ---------------------------------------------------------------------------


def _normalise_rel(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecoveryError("snapshot path must be a non-empty string")
    if "\x00" in value or "\\" in value:
        raise RecoveryError(f"unsafe snapshot path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise RecoveryError(f"absolute snapshot path: {value!r}")
    for part in path.parts:
        if part in ("", ".", "..") or part.endswith((" ", ".")) or ":" in part:
            raise RecoveryError(f"unsafe snapshot path: {value!r}")
    text = path.as_posix()
    if text != value:
        raise RecoveryError(f"non-canonical snapshot path: {value!r}")
    return text


def _parts(rel: str) -> tuple[str, ...]:
    return PurePosixPath(rel).parts


def _long_path(path: Path | str) -> Path:
    """Return a Windows extended-length path for filesystem calls.

    Manifest paths and receipts stay normal.  Only operations that may cross MAX_PATH use
    ``\\\\?\\`` (local) or ``\\\\?\\UNC\\`` (UNC); non-Windows is unchanged.
    """
    if os.name != "nt":
        return Path(path)
    text = os.fspath(path)
    if text.startswith("\\\\?\\"):
        return Path(text)
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _rmtree_long(path: Path | str, *, ignore_errors: bool = False) -> None:
    """Remove a tree using extended paths for every child (Windows MAX_PATH safe)."""
    root = _long_path(path)
    if not root.exists():
        return

    def remove(current: Path) -> None:
        try:
            entries = list(os.scandir(current))
        except OSError:
            if ignore_errors:
                return
            raise
        for entry in entries:
            child = _long_path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    remove(child)
                else:
                    try:
                        os.unlink(child)
                    except PermissionError:
                        os.chmod(child, stat.S_IWRITE | stat.S_IREAD)
                        os.unlink(child)
            except OSError:
                if not ignore_errors:
                    raise
        try:
            os.rmdir(current)
        except PermissionError:
            try:
                os.chmod(current, stat.S_IWRITE | stat.S_IREAD)
                os.rmdir(current)
            except OSError:
                if not ignore_errors:
                    raise
        except OSError:
            if not ignore_errors:
                raise

    remove(root)


def _same_volume(first: Path, second: Path) -> bool:
    try:
        return os.stat(_long_path(first)).st_dev == os.stat(_long_path(second)).st_dev
    except OSError:
        return False


def _is_cross_device(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 17 or getattr(exc, "errno", None) == errno.EXDEV


def _copy_tree_long(source: Path, target: Path) -> None:
    """Copy a verified staging tree using extended paths; target must be an empty directory."""
    source_root = _long_path(source)
    target_root = _long_path(target)
    if not source_root.is_dir():
        raise RecoveryError(f"staging tree is not a directory: {source}")
    if not target_root.is_dir():
        raise RecoveryError(f"delivery staging directory is missing: {target}")

    def copy_dir(src: Path, dst: Path) -> None:
        try:
            entries = list(os.scandir(src))
        except OSError as exc:
            raise RecoveryError(f"cannot read staging directory {src}: {exc}") from exc
        for entry in entries:
            child_src = _long_path(entry.path)
            child_dst = _long_path(dst / entry.name)
            try:
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink():
                    raise RecoveryError(f"staging tree unexpectedly contains a symlink: {entry.path}")
                if entry.is_dir(follow_symlinks=False):
                    child_dst.mkdir()
                    copy_dir(child_src, child_dst)
                    os.utime(child_dst, ns=(info.st_atime_ns, info.st_mtime_ns))
                    os.chmod(child_dst, stat.S_IMODE(info.st_mode))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise RecoveryError(f"staging tree contains a special file: {entry.path}")
                copied = 0
                with child_src.open("rb") as source_stream, child_dst.open("xb") as target_stream:
                    while True:
                        chunk = source_stream.read(CHUNK)
                        if not chunk:
                            break
                        target_stream.write(chunk)
                        copied += len(chunk)
                if copied != info.st_size:
                    raise RecoveryError(f"short copy from staging: {entry.path}")
                os.utime(child_dst, ns=(info.st_atime_ns, info.st_mtime_ns))
                os.chmod(child_dst, stat.S_IMODE(info.st_mode))
            except OSError as exc:
                raise RecoveryError(f"cannot copy staging member {entry.path}: {exc}") from exc
        os.utime(dst, ns=(os.stat(src).st_atime_ns, os.stat(src).st_mtime_ns))
        os.chmod(dst, stat.S_IMODE(os.stat(src).st_mode))

    copy_dir(source_root, target_root)


def _normalise_pattern(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryError(f"{label} pattern must be non-empty")
    text = value.strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if not text or text.startswith("/") or ":" in text.split("/")[0]:
        raise RecoveryError(f"{label} must be workspace-relative: {value!r}")
    path = PurePosixPath(text)
    if any(part in ("", ".", "..") for part in path.parts):
        raise RecoveryError(f"{label} may not escape the workspace: {value!r}")
    return path.as_posix()


def _normalise_include(value: str) -> str:
    return _normalise_pattern(value, "include")


def _normalise_exclude(value: str) -> str:
    return _normalise_pattern(value, "exclude")


def _matches_pattern(rel: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return False
    for pattern in patterns:
        if rel == pattern or rel.startswith(pattern.rstrip("/") + "/"):
            return True
        if fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def _matches_include(rel: str, patterns: tuple[str, ...]) -> bool:
    return _matches_pattern(rel, patterns)


def _matches_exclude(rel: str, patterns: tuple[str, ...]) -> bool:
    return _matches_pattern(rel, patterns)


def _include_may_descend(rel: str, patterns: tuple[str, ...]) -> bool:
    """Whether a soft-excluded directory can contain an explicitly included member."""
    if not patterns:
        return False
    prefix = rel.rstrip("/") + "/"
    for pattern in patterns:
        if pattern == rel or pattern.startswith(prefix):
            return True
        if _matches_include(rel, (pattern,)):
            return True
        if pattern.startswith("**"):
            return True
        wildcard = min((index for index in (pattern.find("*"), pattern.find("?")) if index >= 0),
                       default=len(pattern))
        static = pattern[:wildcard].rstrip("/")
        if static and (static == rel or static.startswith(prefix)):
            return True
    return False


def _is_sqlite_sidecar(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in SQLITE_SIDECAR_SUFFIXES)


def _hard_reason(rel: str, name: str) -> str | None:
    for part in _parts(rel):
        if part.lower() in (".git", ".hg", ".svn"):
            return "git-metadata"
        if part.lower() in HARD_DIR_NAMES:
            return "credential-directory"
    if _is_sqlite_sidecar(name):
        return "sqlite-sidecar"
    lowered = name.lower()
    if lowered in ENV_EXAMPLE_NAMES:
        return None
    if lowered in HARD_FILE_NAMES or lowered.startswith(".env.") or lowered == ".env":
        return "credential-file"
    if any(lowered.startswith(prefix) for prefix in HARD_FILE_PREFIXES):
        return "private-key"
    if lowered.endswith(HARD_FILE_SUFFIXES):
        return "private-key"
    return None


def _soft_reason(rel: str, name: str) -> str | None:
    for part in _parts(rel):
        if part.lower() in SOFT_DIR_NAMES:
            return f"excluded:{part}"
    if name in SOFT_FILE_NAMES or name.lower().endswith(SOFT_FILE_SUFFIXES):
        return "excluded:build-or-cache"
    return None


# ---------------------------------------------------------------------------
# Reading and hashing
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _zip_info(rel: str, src_stat: os.stat_result) -> zipfile.ZipInfo:
    stamp = time.localtime(src_stat.st_mtime)
    year = min(max(stamp.tm_year, 1980), 2107)
    info = zipfile.ZipInfo(rel, (year, stamp.tm_mon, stamp.tm_mday,
                                 stamp.tm_hour, stamp.tm_min, stamp.tm_sec))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | stat.S_IMODE(src_stat.st_mode)) << 16
    return info


def _write_payload(zf: zipfile.ZipFile, info: zipfile.ZipInfo, path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source, zf.open(info, "w") as target:
        while True:
            chunk = source.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            target.write(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(16)
        if header == b"SQLite format 3\x00":
            return True
        if path.suffix.lower() in (".sqlite", ".sqlite3", ".db"):
            sidecars = [Path(str(path) + suffix) for suffix in ("-wal", "-shm")]
            return path.stat().st_size == 0 and any(item.exists() for item in sidecars)
    except OSError:
        return False
    return False


def _remove_sqlite_temp(path: Path) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)


def _sqlite_snapshot(path: Path) -> tuple[Path, dict]:
    fd, raw = tempfile.mkstemp(prefix=".recovery-sqlite-", suffix=".sqlite")
    os.close(fd)
    backup = Path(raw)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=20)) as source:
            source.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(str(backup), timeout=20)) as target:
                source.backup(target)
                target.commit()
        original_hash, original_size = _hash_file(path)
        backup_hash, backup_size = _hash_file(backup)
        sidecars = []
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(str(path) + suffix)
            if side.is_file():
                side_hash, side_size = _hash_file(side)
                sidecars.append({"suffix": suffix, "bytes": side_size, "sha256": side_hash})
        return backup, {
            "method": "sqlite3.backup",
            "sqlite_version": sqlite3.sqlite_version,
            "original": {"bytes": original_size, "sha256": original_hash},
            "backup": {"bytes": backup_size, "sha256": backup_hash},
            "changed": (original_hash, original_size) != (backup_hash, backup_size),
            "sidecars": sidecars,
        }
    except sqlite3.Error as exc:
        _remove_sqlite_temp(backup)
        raise RecoveryError(f"cannot create a consistent SQLite backup for {path}: {exc}") from exc
    except Exception:
        _remove_sqlite_temp(backup)
        raise


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _select(root: Path, includes: tuple[str, ...], excludes: tuple[str, ...]):
    files: list[dict] = []
    directories: list[str] = []
    skipped: list[dict] = []
    repos: list[dict] = []
    seen: dict[str, str] = {}
    seen_dirs: dict[str, str] = {}

    def add_skip(rel: str, reason: str) -> None:
        skipped.append({"path": rel, "reason": reason})

    def repo_selected(rel_dir: str, forced: bool) -> bool:
        if not rel_dir:
            return True
        parent = PurePosixPath(rel_dir).name
        return forced or _soft_reason(rel_dir, parent) is None

    def walk(directory: Path, rel_dir: str, forced: bool) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise RecoveryError(f"cannot read directory {directory}: {exc}") from exc
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            try:
                rel = _normalise_rel(rel)
            except RecoveryError:
                add_skip(rel, "unsafe-name")
                continue
            if entry.is_symlink():
                add_skip(rel, "symlink")
                continue
            if _matches_exclude(rel, excludes):
                add_skip(rel, "excluded")
                continue
            name = entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise RecoveryError(f"cannot stat {entry.path}: {exc}") from exc
            if name == ".git":
                if repo_selected(rel_dir, forced):
                    if entry.is_dir(follow_symlinks=False):
                        repos.append({"path": rel_dir or ".", "root": directory, "kind": "dir"})
                    elif entry.is_file(follow_symlinks=False):
                        repos.append({"path": rel_dir or ".", "root": directory, "kind": "file"})
                add_skip(rel, "git-metadata")
                continue
            hard = _hard_reason(rel, name)
            if hard:
                add_skip(rel, hard)
                continue
            soft = _soft_reason(rel, name)
            if entry.is_dir(follow_symlinks=False):
                if soft and not forced and not _include_may_descend(rel, includes):
                    add_skip(rel, soft)
                    continue
                dir_key = rel.casefold()
                if dir_key in seen_dirs or dir_key in seen:
                    raise RecoveryError(f"case-insensitive path collision: {rel!r}")
                seen_dirs[dir_key] = rel
                child_forced = forced or _matches_include(rel, includes)
                directories.append(rel)
                walk(Path(entry.path), rel, child_forced)
                continue
            if not entry.is_file(follow_symlinks=False):
                add_skip(rel, "special-file")
                continue
            if soft and not forced and not _matches_include(rel, includes):
                add_skip(rel, soft)
                continue
            key = rel.casefold()
            if key in seen or key in seen_dirs:
                raise RecoveryError(f"case-insensitive duplicate path: {rel!r} and {seen.get(key, seen_dirs.get(key))!r}")
            seen[key] = rel
            files.append({"path": rel, "source": Path(entry.path), "stat": info})

    walk(root, "", False)
    files.sort(key=lambda item: item["path"].casefold())
    directories.sort(key=str.casefold)
    skipped.sort(key=lambda item: (item["path"].casefold(), item["reason"]))
    repos.sort(key=lambda item: (len(PurePosixPath(item["path"]).parts), item["path"].casefold()), reverse=True)
    return files, directories, skipped, repos


# ---------------------------------------------------------------------------
# Git history (local commands only; no config copy, no network)
# ---------------------------------------------------------------------------


def _git_env(hooks: Path | None) -> dict:
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "file",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
    })
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR"):
        env.pop(key, None)
    if hooks is not None:
        env["GIT_HOOKS_PATH"] = str(_long_path(hooks))
    return env


def _run_git(repo: Path, args: list[str], *, hooks: Path | None = None, check: bool = True):
    command = ["git"]
    hooks_arg = _long_path(hooks) if hooks is not None else None
    if hooks_arg is not None:
        command += ["-c", f"core.hooksPath={hooks_arg}"]
    command += ["-C", str(_long_path(repo)), *args]
    try:
        result = subprocess.run(
            command, env=_git_env(hooks), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
    except FileNotFoundError as exc:
        raise RecoveryError("git executable not found; cannot export or rebuild Git history") from exc
    except subprocess.TimeoutExpired as exc:
        raise RecoveryError(f"git command timed out in {repo}: {args[0]}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        raise RecoveryError(
            f"git {' '.join(args[:2])} failed for {repo}: "
            + (detail[-1] if detail else f"exit {result.returncode}"))
    return result


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_gitdir(repo: Path, workspace: Path) -> None:
    dotgit = repo / ".git"
    if dotgit.is_symlink():
        raise RecoveryError(f"Git metadata is a symlink; refusing to follow: {dotgit}")
    if dotgit.is_dir():
        gitdir = dotgit.resolve()
    elif dotgit.is_file():
        try:
            first = dotgit.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, UnicodeDecodeError, IndexError) as exc:
            raise RecoveryError(f"unreadable .git pointer: {dotgit}") from exc
        if not first.lower().startswith("gitdir:"):
            raise RecoveryError(f"unsupported .git pointer: {dotgit}")
        raw = first.split(":", 1)[1].strip()
        target = Path(raw)
        if not target.is_absolute():
            target = repo / target
        gitdir = target.resolve()
        if not gitdir.exists():
            raise RecoveryError(f"git worktree gitdir does not exist: {gitdir}")
    else:
        raise RecoveryError(f"Git metadata is missing or not a file/directory: {dotgit}")
    if not _inside(gitdir, workspace):
        raise RecoveryError(f"git metadata points outside workspace; refusing to read it: {gitdir}")
    common = gitdir / "commondir"
    if common.is_file():
        try:
            pointer = common.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise RecoveryError(f"unreadable git commondir pointer: {common}") from exc
        common_target = Path(pointer)
        if not common_target.is_absolute():
            common_target = gitdir / common_target
        if not _inside(common_target, workspace):
            raise RecoveryError(f"git common dir points outside workspace; refusing to read it: {common_target}")


def _git_repo_info(repo: Path, rel: str, workspace: Path, bundle_dir: Path, hooks: Path):
    _validate_gitdir(repo, workspace)
    probe = _run_git(repo, ["rev-parse", "--is-inside-work-tree"], hooks=hooks, check=False)
    if probe.returncode != 0 or probe.stdout.strip().lower() != "true":
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()
        raise RecoveryError(
            f"not a usable Git work tree: {repo}: " + (detail[-1] if detail else "unknown"))
    head_result = _run_git(repo, ["rev-parse", "--verify", "HEAD"], hooks=hooks, check=False)
    if head_result.returncode == 0:
        head = head_result.stdout.strip()
        unborn = False
    else:
        detail = (head_result.stderr or head_result.stdout).lower()
        if not ("needed a single revision" in detail or "unknown revision" in detail
                or "ambiguous argument 'head'" in detail):
            raise RecoveryError(f"cannot read HEAD for {repo}: {(head_result.stderr or '').strip()}")
        head = None
        unborn = True
    branch_result = _run_git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"],
                             hooks=hooks, check=False)
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    if branch_result.returncode not in (0, 1):
        raise RecoveryError(f"cannot read current branch for {repo}")
    status = _run_git(repo, ["status", "--porcelain"], hooks=hooks)
    info = {
        "path": rel or ".",
        "kind": "git",
        "head": head,
        "branch": branch,
        "dirty": bool(status.stdout.strip()),
        "unborn": unborn,
        "bundle": None,
        "bundle_bytes": 0,
        "bundle_sha256": None,
    }
    bundle = None
    if head is not None:
        slug = hashlib.sha256((rel + "\0" + head).encode("utf-8")).hexdigest()[:16]
        bundle = bundle_dir / f"{slug}.bundle"
        _run_git(repo, ["bundle", "create", str(bundle), "--all", "HEAD"], hooks=hooks)
        _run_git(repo, ["bundle", "verify", str(bundle)], hooks=hooks)
        digest, size = _hash_file(bundle)
        info.update({"bundle_bytes": size, "bundle_sha256": digest})
    return info, bundle


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _default_archive(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = root / DEFAULT_ARCHIVE_DIR
    candidate = folder / f"recovery-{stamp}.zip"
    index = 2
    while candidate.exists():
        candidate = folder / f"recovery-{stamp}-{index}.zip"
        index += 1
    return candidate


def _runtime_info() -> dict:
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "zip": "zipfile/deflate",
        "requirements": {
            "python": ">=3.11",
            "node_plugins": ">=20",
            "node_dsh_zstd_logs": ">=24",
            "feishu_crypto": "cryptography (optional)",
            "os": "Windows scheduled task requires pythonw/pwsh; no task is registered by this tool",
        },
        "dsh_profile": os.environ.get("DSH_PROFILE") or None,
        "dsh_contract": "public baseline 0.2.0-rc.2; re-check host contracts after restore",
    }


def _warning(archive: Path, root: Path) -> str:
    try:
        archive.relative_to(root / ".local")
        location = "在 .local 内"
    except ValueError:
        location = "不在 .local 内，可能被 Git 跟踪"
    return (
        "私密恢复快照：含源码、现行文档、专题、运行数据库与 Git 历史，可能含会话/机器标识；"
        f"该文件{location}。禁止提交、公开或发送给无关方；请把目标目录加入 .gitignore。"
    )


def export(workspace, archive=None, *, include: Iterable[str] = (),
           exclude: Iterable[str] = ()) -> dict:
    """Export the current filesystem worktree (not Git HEAD) to a private zip.

    ``archive`` is either an explicit path or omitted.  When omitted the archive is placed under
    ``<workspace>/.local/recovery/``.  ``include`` is additive: it can re-enable a soft-excluded
    path such as ``data/one-file.csv``.  ``exclude`` is subtractive and wins over ``include``.
    Credentials, private keys and SQLite sidecars remain hard-excluded.  Consistency is
    file-by-file; SQLite is independently backed up, not a transaction across the whole tree.
    Local Git history is bundled, but ``.git`` metadata/config is not copied.
    """
    root = Path(workspace).expanduser().resolve()
    if not root.is_dir():
        raise RecoveryError(f"workspace is not a directory: {root}")
    archive_path = Path(archive).expanduser().resolve() if archive is not None else _default_archive(root)
    if archive_path.suffix.lower() != ".zip":
        raise RecoveryError("archive must end in .zip")
    if archive_path.exists() or archive_path.is_symlink():
        raise RecoveryError(f"refusing to overwrite an existing archive: {archive_path}")
    if archive_path == root:
        raise RecoveryError("archive cannot be the workspace root")
    includes = tuple(sorted({_normalise_include(item) for item in include}))
    excludes = tuple(sorted({_normalise_exclude(item) for item in exclude}))
    files, directories, skipped, git_repos = _select(root, includes, excludes)
    if any(item["path"].casefold() == MANIFEST_NAME.casefold() for item in files):
        raise RecoveryError(f"workspace contains the reserved snapshot manifest name: {MANIFEST_NAME}")
    if any(item["path"].startswith(GIT_BUNDLE_PREFIX) for item in files):
        raise RecoveryError(f"workspace contains the reserved Git bundle prefix: {GIT_BUNDLE_PREFIX}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    part = archive_path.with_name(f".{archive_path.name}.part-{os.getpid()}-{time.time_ns()}")
    entries: list[dict] = []
    sqlite_count = 0
    git_bundles = 0
    git_infos: list[dict] = []
    git_temp = None
    try:
        if git_repos:
            git_temp = tempfile.TemporaryDirectory(prefix=".recovery-git-")
            git_root = Path(git_temp.name)
            (git_root / "bundles").mkdir()
            (git_root / "hooks").mkdir()
        with zipfile.ZipFile(part, "x", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for item in files:
                source = item["source"]
                rel = item["path"]
                info = _zip_info(rel, item["stat"])
                sqlite_meta = None
                payload = source
                backup = None
                try:
                    if _is_sqlite(source):
                        backup, sqlite_meta = _sqlite_snapshot(source)
                        payload = backup
                    digest, size = _write_payload(zf, info, payload)
                finally:
                    if backup is not None:
                        _remove_sqlite_temp(backup)
                if sqlite_meta is None:
                    after = source.stat()
                    if after.st_size != item["stat"].st_size or after.st_mtime_ns != item["stat"].st_mtime_ns:
                        raise RecoveryError(f"file changed during export: {rel}")
                    if size != item["stat"].st_size:
                        raise RecoveryError(f"short read during export: {rel}")
                else:
                    sqlite_count += 1
                entry = {
                    "path": rel,
                    "kind": "file",
                    "bytes": size,
                    "sha256": digest,
                    "mode": stat.S_IMODE(item["stat"].st_mode),
                    "mtime_ns": item["stat"].st_mtime_ns,
                }
                if sqlite_meta is not None:
                    entry["sqlite"] = sqlite_meta
                entries.append(entry)
            if git_repos:
                bundle_dir = git_root / "bundles"
                hooks = git_root / "hooks"
                for index, repo in enumerate(git_repos):
                    info, bundle = _git_repo_info(repo["root"], repo["path"], root, bundle_dir, hooks)
                    if bundle is not None:
                        member = f"{GIT_BUNDLE_PREFIX}{index:04d}.bundle"
                        zinfo = _zip_info(member, bundle.stat())
                        digest, size = _write_payload(zf, zinfo, bundle)
                        info.update({"bundle": member, "bundle_bytes": size, "bundle_sha256": digest})
                        git_bundles += 1
                    git_infos.append(info)
            manifest = {
                "format": FORMAT,
                "version": VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": {
                    "workspace_name": root.name,
                    "worktree": "filesystem",
                    "includes_uncommitted": True,
                    "heads_not_used": True,
                },
                "consistency": {
                    "scope": "file-by-file",
                    "files": "non-SQLite files are stat-checked before/after their own read",
                    "sqlite": "each database is independently consistent via sqlite3.backup",
                    "tree": "not a point-in-time transaction; files changed after their read are not rolled back",
                },
                "git": {
                    "metadata_included": False,
                    "config_included": False,
                    "history_bundles_included": bool(git_bundles),
                    "head_commit_included": any(item["head"] for item in git_infos),
                    "worktrees_restorable": False,
                    "reason": ".git/.hg/.svn config/index/worktree pointers are not copied; history is stored as local bundles",
                    "action": "explicit restore --rebuild-git: init, fetch bundle, reset --mixed HEAD; do not run the original Git flow from pointers",
                    "repositories": git_infos,
                },
                "selection": {
                    "include": list(includes),
                    "exclude": list(excludes),
                    "soft_excludes": sorted(SOFT_DIR_NAMES | SOFT_FILE_NAMES),
                    "soft_file_suffixes": list(SOFT_FILE_SUFFIXES),
                    "hard_excludes": sorted(HARD_DIR_NAMES | HARD_FILE_NAMES),
                    "hard_file_prefixes": list(HARD_FILE_PREFIXES),
                    "hard_file_suffixes": list(HARD_FILE_SUFFIXES) + list(SQLITE_SIDECAR_SUFFIXES),
                    "sqlite_sidecars": "hard-excluded; never restored independently, include cannot override",
                    "symlinks": "skipped, never followed",
                    "sqlite": "sqlite3.backup from a read-only connection",
                },
                "runtime": _runtime_info(),
                "runtime_state": {
                    "status": "unknown",
                    "resumed": False,
                    "reason": "快照保存字节和一致性数据库备份；旧会话、租约、任务与未完成运行不自动续跑",
                    "rebind": [dict(item) for item in REBIND_ITEMS],
                },
                "rebind_required": [dict(item) for item in REBIND_ITEMS],
                "state_inventory": {
                    "sqlite_snapshots": [entry["path"] for entry in entries if "sqlite" in entry],
                    "live_host_state": "not included: DSH global, sessions, tasks, credentials",
                    "status": "unknown",
                },
                "entries": entries,
                "directories": directories,
                "skipped": skipped,
                "notes": [
                    "包含当前工作树，不是 Git HEAD 的提交快照。",
                    "文件级快照：非 SQLite 文件各自读前/读后 stat 核对；不保证整棵树在同一时点冻结。",
                    "SQLite 条目是 sqlite3.backup 结果；原始文件与备份的字节/hash 差异见各条目的 sqlite 字段。",
                    "SQLite 的 -wal/-shm/-journal 是硬排除项；即使 include 也不能独立恢复，避免旧 sidecar 覆盖一致备份。",
                    "运行态保持 unknown；恢复后必须实际重绑任务与会话，不得把旧库当新库直接推进。",
                    "不包含 .git/.hg/.svn 元数据；历史用本地 git bundle 保存，恢复后必须显式 --rebuild-git 重建仓库。",
                    "Git bundle 只含 refs/objects，不复制 config 凭据；但历史提交可能含敏感内容，整个包始终私密。",
                    "凭据、DSH 全局、data/submission/archive/temp、venv/node_modules 默认排除；凭据不能用 include 加入。",
                ],
            }
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        if archive_path.exists():
            raise RecoveryError(f"archive appeared during export: {archive_path}")
        os.replace(part, archive_path)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    finally:
        if git_temp is not None:
            git_temp.cleanup()
    archive_hash, archive_bytes = _hash_file(archive_path)
    return {
        "ok": True,
        "archive": str(archive_path),
        "archive_bytes": archive_bytes,
        "archive_sha256": archive_hash,
        "entries": len(entries),
        "directories": len(directories),
        "sqlite": sqlite_count,
        "git_repositories": len(git_infos),
        "git_bundles": git_bundles,
        "skipped": len(skipped),
        "consistency": "file-by-file; SQLite independently consistent; tree not transactional",
        "git_metadata": False,
        "worktrees_restorable": False,
        "runtime_state": "unknown",
        "rebind_required": [dict(item) for item in REBIND_ITEMS],
        "warning": _warning(archive_path, root),
    }


# ---------------------------------------------------------------------------
# Verify / restore
# ---------------------------------------------------------------------------


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    names = zf.namelist()
    if names.count(MANIFEST_NAME) != 1:
        raise RecoveryError("archive must contain exactly one recovery manifest")
    folded = [name.casefold() for name in names]
    duplicates = sorted(name for name, count in Counter(folded).items() if count > 1)
    if duplicates:
        raise RecoveryError(f"duplicate archive member(s): {duplicates[:3]}")
    manifest_info = next((info for info in zf.infolist() if info.filename == MANIFEST_NAME), None)
    if manifest_info is None or manifest_info.is_dir() or manifest_info.filename.endswith("/"):
        raise RecoveryError("manifest must be a regular file")
    if manifest_info.flag_bits & 0x1:
        raise RecoveryError("encrypted recovery manifest is not supported")
    if stat.S_ISLNK(manifest_info.external_attr >> 16):
        raise RecoveryError("symlink recovery manifest is not allowed")
    try:
        raw = zf.read(MANIFEST_NAME)
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RuntimeError, NotImplementedError) as exc:
        raise RecoveryError(f"unreadable recovery manifest: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise RecoveryError("not an autoresearch recovery snapshot")
    if manifest.get("version") != VERSION:
        raise RecoveryError(f"unsupported recovery snapshot version: {manifest.get('version')!r}")
    entries = manifest.get("entries")
    directories = manifest.get("directories")
    if not isinstance(entries, list) or not isinstance(directories, list):
        raise RecoveryError("manifest entries/directories must be lists")
    if not isinstance(manifest.get("runtime_state", {}), dict):
        raise RecoveryError("manifest runtime_state must be an object")
    if not isinstance(manifest.get("git", {}), dict):
        raise RecoveryError("manifest git must be an object")
    return manifest


def _inspect(zf: zipfile.ZipFile):
    manifest = _read_manifest(zf)
    infos = {info.filename: info for info in zf.infolist() if info.filename != MANIFEST_NAME}
    expected: dict[str, dict] = {}
    folded: dict[str, str] = {}
    for entry in manifest["entries"]:
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            raise RecoveryError("manifest entry must describe a regular file")
        rel = _normalise_rel(entry.get("path"))
        if _is_sqlite_sidecar(PurePosixPath(rel).name):
            raise RecoveryError(f"SQLite sidecar cannot be restored independently: {rel}")
        if rel.startswith(GIT_BUNDLE_PREFIX):
            raise RecoveryError(f"normal entry uses the reserved Git bundle prefix: {rel}")
        if rel in expected or rel.casefold() in folded:
            raise RecoveryError(f"duplicate manifest path: {rel}")
        size = entry.get("bytes")
        digest = entry.get("sha256")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise RecoveryError(f"bad bytes/sha256 for {rel}")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise RecoveryError(f"bad sha256 for {rel}") from exc
        if not isinstance(entry.get("mode", 0), int) or not isinstance(entry.get("mtime_ns", 0), int):
            raise RecoveryError(f"bad mode/mtime for {rel}")
        if "sqlite" in entry and not isinstance(entry["sqlite"], dict):
            raise RecoveryError(f"bad sqlite metadata for {rel}")
        expected[rel] = entry
        folded[rel.casefold()] = rel
    dirs: set[str] = set()
    dir_folded: dict[str, str] = {}
    for raw in manifest["directories"]:
        rel = _normalise_rel(raw)
        key = rel.casefold()
        if rel in dirs or key in dir_folded:
            raise RecoveryError(f"duplicate manifest directory: {rel}")
        if key in folded:
            raise RecoveryError(f"manifest path is both file and directory: {rel}")
        dirs.add(rel)
        dir_folded[key] = rel
    for rel in expected:
        key = rel.casefold()
        if key in dir_folded:
            raise RecoveryError(f"manifest path is both file and directory: {rel}")
    for rel in dirs:
        parts = _parts(rel)
        for index in range(1, len(parts)):
            parent = PurePosixPath(*parts[:index]).as_posix()
            if parent.casefold() in folded:
                raise RecoveryError(f"manifest file is an ancestor of directory: {parent}")
    git = manifest.get("git", {})
    if not isinstance(git, dict) or not isinstance(git.get("repositories", []), list):
        raise RecoveryError("manifest git.repositories must be a list")
    bundles: dict[str, dict] = {}
    repo_paths: set[str] = set()
    for repo in git.get("repositories", []):
        if not isinstance(repo, dict):
            raise RecoveryError("manifest git repository must be an object")
        repo_path = repo.get("path", ".")
        if repo_path != ".":
            _normalise_rel(repo_path)
        if repo_path in repo_paths:
            raise RecoveryError(f"duplicate Git repository path: {repo_path}")
        repo_paths.add(repo_path)
        name = repo.get("bundle")
        if not name:
            continue
        rel = _normalise_rel(name)
        if not rel.startswith(GIT_BUNDLE_PREFIX):
            raise RecoveryError(f"Git bundle outside the reserved prefix: {rel}")
        if rel in bundles or rel.casefold() in {item.casefold() for item in bundles}:
            raise RecoveryError(f"duplicate Git bundle: {rel}")
        if rel in expected or rel.casefold() in folded or rel in dirs or rel.casefold() in dir_folded:
            raise RecoveryError(f"Git bundle collides with a restored path: {rel}")
        size = repo.get("bundle_bytes")
        digest = repo.get("bundle_sha256")
        if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise RecoveryError(f"bad Git bundle metadata: {rel}")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise RecoveryError(f"bad Git bundle sha256: {rel}") from exc
        bundles[rel] = {"bytes": size, "sha256": digest}
    if set(infos) != set(expected) | set(bundles):
        missing = sorted((set(expected) | set(bundles)) - set(infos))[:3]
        extra = sorted(set(infos) - (set(expected) | set(bundles)))[:3]
        raise RecoveryError(f"archive/manifest mismatch; missing={missing}, extra={extra}")
    for rel, info in infos.items():
        if info.is_dir() or info.filename.endswith("/"):
            raise RecoveryError(f"unexpected directory member: {rel}")
        if info.flag_bits & 0x1:
            raise RecoveryError(f"encrypted archive member is not supported: {rel}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise RecoveryError(f"symlink archive member is not allowed: {rel}")
        expected_size = expected.get(rel, bundles.get(rel, {})).get("bytes")
        if expected_size is None or info.file_size != expected_size:
            raise RecoveryError(f"size mismatch for {rel}")
    # Hash every member before restore is allowed to create even a staging directory.
    for rel in sorted(set(expected) | set(bundles)):
        digest = hashlib.sha256()
        try:
            with zf.open(infos[rel], "r") as stream:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
        except (OSError, RuntimeError, NotImplementedError) as exc:
            raise RecoveryError(f"cannot read archive member {rel}: {exc}") from exc
        if digest.hexdigest() != (expected.get(rel) or bundles[rel])["sha256"]:
            raise RecoveryError(f"sha256 mismatch for {rel}")
    return manifest, expected, infos, dirs, bundles


def _archive_digest(path: Path) -> tuple[str, int]:
    return _hash_file(path)


def verify(archive) -> dict:
    """Verify every member and path without writing anything."""
    path = Path(archive).expanduser().resolve()
    if not path.is_file():
        raise RecoveryError(f"archive not found: {path}")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            manifest, expected, _infos, _dirs, bundles = _inspect(zf)
    except zipfile.BadZipFile as exc:
        raise RecoveryError(f"not a readable zip archive: {exc}") from exc
    archive_hash, archive_bytes = _archive_digest(path)
    return {
        "ok": True,
        "archive": str(path),
        "archive_bytes": archive_bytes,
        "archive_sha256": archive_hash,
        "entries": len(expected),
        "directories": len(manifest["directories"]),
        "sqlite": sum(1 for entry in expected.values() if "sqlite" in entry),
        "git_bundles": len(bundles),
        "git_repositories": len(manifest.get("git", {}).get("repositories", [])),
        "consistency": manifest.get("consistency", {}),
        "git_metadata": manifest.get("git", {}).get("metadata_included", False),
        "worktrees_restorable": manifest.get("git", {}).get("worktrees_restorable", False),
        "runtime_state": manifest.get("runtime_state", {}).get("status", "unknown"),
        "rebind_required": manifest.get("runtime_state", {}).get("rebind", []),
        "manifest": manifest,
    }


def _safe_target(target: Path) -> Path:
    raw = target.expanduser()
    normal = Path(os.path.abspath(os.fspath(raw)))
    checked = _long_path(normal)
    if checked.is_symlink():
        raise RecoveryError(f"target is a symlink; refusing to follow it: {normal}")
    if checked.exists():
        raise RecoveryError(f"target already exists; refusing to overwrite: {normal}")
    if normal.name in ("", ".", ".."):
        raise RecoveryError("target must be a new directory")
    return normal


def _rebuild_git(staging: Path, repositories: list[dict], bundles: dict[str, Path],
                 template: Path, hooks: Path) -> list[dict]:
    """Create normal repositories in staging; never checkout or touch working-tree bytes."""
    rebuilt = []
    ordered = sorted(repositories, key=lambda item: (
        len(PurePosixPath(item.get("path", ".") if item.get("path") != "." else ".").parts),
        str(item.get("path", "."))), reverse=True)
    for repo in ordered:
        rel = repo.get("path", ".")
        repo_dir = staging if rel == "." else staging / Path(*PurePosixPath(rel).parts)
        repo_fs = _long_path(repo_dir)
        if not repo_fs.is_dir():
            raise RecoveryError(f"restored Git repository directory is missing: {rel}")
        dotgit = repo_fs / ".git"
        if dotgit.exists() or dotgit.is_symlink():
            raise RecoveryError(f"restored tree unexpectedly contains Git metadata: {rel}/.git")
        _run_git(repo_dir, ["init", "-q", "--template", str(_long_path(template))], hooks=hooks)
        head = repo.get("head")
        if head:
            bundle = bundles.get(repo.get("bundle"))
            if bundle is None:
                raise RecoveryError(f"Git repository {rel} has no verified history bundle")
            _run_git(repo_dir, ["fetch", "--force", "--update-head-ok", str(bundle),
                                "+refs/*:refs/*"], hooks=hooks)
            branch = repo.get("branch")
            if branch:
                _run_git(repo_dir, ["update-ref", f"refs/heads/{branch}", str(head)], hooks=hooks)
                _run_git(repo_dir, ["symbolic-ref", "HEAD", f"refs/heads/{branch}"], hooks=hooks)
            else:
                _run_git(repo_dir, ["update-ref", "--no-deref", "HEAD", str(head)], hooks=hooks)
            _run_git(repo_dir, ["reset", "--mixed", "HEAD"], hooks=hooks)
        rebuilt.append({"path": rel, "head": head, "branch": repo.get("branch"),
                        "unborn": bool(repo.get("unborn"))})
    return rebuilt


def restore(archive, target, *, rebuild_git: bool = False) -> dict:
    """Verify the archive completely, then atomically create a brand-new target directory.

    ``rebuild_git`` is explicit and defaults to false.  When true, each repository is initialized
    from its verified bundle, then ``git reset --mixed HEAD`` updates only the index; restored
    working-tree bytes are never checked out or overwritten.  Cross-volume delivery copies the
    verified staging tree to a second staging directory under the target parent, then renames
    atomically; no Git command runs in that second staging directory.
    """
    archive_path = Path(archive).expanduser().resolve()
    if not archive_path.is_file():
        raise RecoveryError(f"archive not found: {archive_path}")
    destination = _safe_target(Path(target))
    parent = destination.parent
    staging = None
    delivery = None
    git_temp = None
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            manifest, expected, infos, dirs, bundles = _inspect(zf)
            archive_hash, archive_bytes = _archive_digest(archive_path)
            _long_path(parent).mkdir(parents=True, exist_ok=True)
            # Build in a short system-temp staging tree: Git for Windows rejects an extended
            # `-C \\?\...` path even though Python file APIs accept it.  The final rename uses
            # extended paths, so the target may still be arbitrarily long.
            staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.restore-"))
            for rel in sorted(dirs):
                _long_path(staging / rel).mkdir(parents=True, exist_ok=True)
            for rel in sorted(expected):
                entry = expected[rel]
                out = _long_path(staging / rel)
                out.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                size = 0
                with zf.open(infos[rel], "r") as source, out.open("xb") as sink:
                    while True:
                        chunk = source.read(CHUNK)
                        if not chunk:
                            break
                        digest.update(chunk)
                        sink.write(chunk)
                        size += len(chunk)
                if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                    raise RecoveryError(f"restored bytes mismatch for {rel}")
                try:
                    os.chmod(out, int(entry.get("mode", 0o644)) & 0o777)
                except OSError:
                    pass
                try:
                    stamp = int(entry.get("mtime_ns", 0))
                    if stamp > 0:
                        os.utime(out, ns=(stamp, stamp))
                except OSError:
                    pass
            rebuilt = []
            if rebuild_git:
                git_temp = tempfile.TemporaryDirectory(prefix=".recovery-git-restore-")
                git_root = Path(git_temp.name)
                template = git_root / "template"
                hooks = git_root / "hooks"
                template.mkdir()
                hooks.mkdir()
                bundle_paths: dict[str, Path] = {}
                for rel in sorted(bundles):
                    out = git_root / "bundles" / Path(rel).name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(infos[rel], "r") as source, out.open("xb") as sink:
                        while True:
                            chunk = source.read(CHUNK)
                            if not chunk:
                                break
                            sink.write(chunk)
                    bundle_paths[rel] = out
                rebuilt = _rebuild_git(staging, manifest.get("git", {}).get("repositories", []),
                                       bundle_paths, template, hooks)
            if _long_path(destination).exists():
                raise RecoveryError(f"target appeared during restore: {destination}")
            delivered = False
            if _same_volume(staging, parent):
                try:
                    os.rename(_long_path(staging), _long_path(destination))
                    staging = None
                    delivered = True
                except OSError as exc:
                    if not _is_cross_device(exc):
                        raise
            if not delivered:
                # Cross-device: copy the already verified staging tree to a second new
                # staging directory on the target volume, then rename from there.  No Git
                # command runs in this second staging directory.
                delivery = Path(tempfile.mkdtemp(
                    prefix=f".{destination.name}.deliver-", dir=_long_path(parent)))
                _copy_tree_long(staging, delivery)
                if _long_path(destination).exists():
                    raise RecoveryError(f"target appeared during restore: {destination}")
                os.rename(_long_path(delivery), _long_path(destination))
                delivery = None
    except zipfile.BadZipFile as exc:
        raise RecoveryError(f"not a readable zip archive: {exc}") from exc
    finally:
        if staging is not None:
            _rmtree_long(staging, ignore_errors=True)
        if delivery is not None:
            _rmtree_long(delivery, ignore_errors=True)
        if git_temp is not None:
            git_temp.cleanup()
    return {
        "ok": True,
        "target": str(destination),
        "archive": str(archive_path),
        "archive_bytes": archive_bytes,
        "archive_sha256": archive_hash,
        "entries": len(expected),
        "directories": len(dirs),
        "sqlite": sum(1 for entry in expected.values() if "sqlite" in entry),
        "git_bundles": len(bundles),
        "git_rebuilt": rebuilt,
        "consistency": manifest.get("consistency", {}),
        "git_metadata": False,
        "worktrees_restorable": False,
        "runtime_state": "unknown",
        "rebind_required": manifest.get("runtime_state", {}).get("rebind", []),
    }


__all__ = [
    "FORMAT", "VERSION", "MANIFEST_NAME", "RecoveryError",
    "export", "verify", "restore",
]
