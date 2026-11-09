# GENERATED-BY: independent recovery tests, 2026-11-09
# LAST-EDIT: 2026-11-09
# PURPOSE: Prove dirty-worktree export, SQLite/WAL backup, privacy excludes and hostile archive rejection.
# ORIGIN: recovery.py public contract; synthetic files only, no network, no real credentials.

from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from unittest import mock

from kernel.autoresearch import recovery, recovery_cli


ROOT = Path(__file__).resolve().parents[1]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_archive(path: Path):
    with zipfile.ZipFile(path) as zf:
        manifest = json.loads(zf.read(recovery.MANIFEST_NAME).decode("utf-8"))
        payloads = {name: zf.read(name) for name in zf.namelist() if name != recovery.MANIFEST_NAME}
    return manifest, payloads


def write_archive(path: Path, manifest: dict, payloads: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(recovery.MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False))
        for name, data in payloads.items():
            zf.writestr(name, data)


def rewrite_member(source: Path, target: Path, member: str, mutate) -> None:
    manifest, payloads = load_archive(source)
    payloads[member] = mutate(payloads[member])
    write_archive(target, manifest, payloads)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "source"
        self.root.mkdir()
        self.writer = None
        self.addCleanup(self.close_writer)
        if shutil.which("git") is None:
            self.skipTest("git is required for the dirty-worktree fixture")
        self.git("init", "-q")
        (self.root / "README.md").write_bytes(b"HEAD\n")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "current.md").write_text("current\n", encoding="utf-8")
        (self.root / "topics" / "t1").mkdir(parents=True)
        (self.root / "topics" / "t1" / "STATUS.md").write_text("unknown\n", encoding="utf-8")
        (self.root / "data").mkdir()
        (self.root / "data" / "keep.txt").write_text("keep\n", encoding="utf-8")
        (self.root / "data" / "secret.csv").write_text("secret\n", encoding="utf-8")
        for rel, text in {
            ".kaggle_auth/token": "not-a-real-token",
            ".ssh/id_rsa": "not-a-real-key",
            ".env": "SECRET=1",
            "private/server.pem": "not-a-real-key",
            "submission/x": "x",
            "archive/old": "old",
            "temp/x": "x",
            ".venv/lib/python": "x",
            "node_modules/pkg/index.js": "x",
        }.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        (self.root / ".env.example").write_text("EXAMPLE=1", encoding="utf-8")
        self.git("add", "README.md")
        self.git("-c", "user.name=Recovery Test", "-c", "user.email=recovery@example.invalid",
                 "commit", "-q", "-m", "head")
        (self.root / "README.md").write_bytes(b"DIRTY WORKTREE\n")
        self.make_sqlite()
        self.archive = self.root / ".local" / "snap.zip"

    def close_writer(self):
        if self.writer is not None:
            try:
                self.writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.writer.close()
            self.writer = None

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        if result.returncode != 0:
            self.fail(result.stderr or result.stdout)

    def make_sqlite(self):
        db = self.root / "state" / "topics.sqlite"
        db.parent.mkdir(parents=True)
        self.writer = sqlite3.connect(str(db))
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.writer.execute("CREATE TABLE topics(id TEXT PRIMARY KEY, state TEXT)")
        self.writer.execute("INSERT INTO topics VALUES ('t1', 'unknown')")
        self.writer.commit()
        self.db = db
        self.assertTrue(Path(str(db) + "-wal").exists())
        self.assertTrue(Path(str(db) + "-shm").exists())

    def export(self, archive=None, include=(), exclude=()):
        archive = archive or self.archive
        return recovery.export(self.root, archive, include=include, exclude=exclude)

    def test_dirty_worktree_and_sqlite_are_restorable(self):
        before_main = self.db.read_bytes()
        before_wal = Path(str(self.db) + "-wal").read_bytes()
        result = self.export()
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(result["entries"], 4)
        self.assertEqual(result["sqlite"], 1)
        self.assertEqual(self.db.read_bytes(), before_main)
        self.assertEqual(Path(str(self.db) + "-wal").read_bytes(), before_wal)

        with zipfile.ZipFile(self.archive) as zf:
            names = set(zf.namelist())
            self.assertIn("README.md", names)
            self.assertIn("topics/t1/STATUS.md", names)
            self.assertIn("state/topics.sqlite", names)
            self.assertNotIn("state/topics.sqlite-wal", names)
            self.assertNotIn("state/topics.sqlite-shm", names)
            self.assertNotIn(".git/HEAD", names)
            self.assertEqual(zf.read("README.md"), b"DIRTY WORKTREE\n")
            self.assertIn(b"unknown", zf.read("state/topics.sqlite"))
        verified = recovery.verify(self.archive)
        self.assertEqual(verified["entries"], result["entries"])
        self.assertEqual(verified["runtime_state"], "unknown")
        sqlite_entry = next(item for item in verified["manifest"]["entries"] if "sqlite" in item)
        self.assertEqual(sqlite_entry["sqlite"]["method"], "sqlite3.backup")
        self.assertTrue(sqlite_entry["sqlite"]["changed"])
        self.assertTrue(sqlite_entry["sqlite"]["sidecars"])
        self.assertEqual(verified["manifest"]["consistency"]["scope"], "file-by-file")
        self.assertFalse(verified["manifest"]["git"]["metadata_included"])
        self.assertFalse(verified["manifest"]["git"]["worktrees_restorable"])
        self.assertTrue(any(item["kind"] == "git_worktrees"
                            for item in verified["manifest"]["rebind_required"]))

        target = Path(self.tmp.name) / "restored"
        restored = recovery.restore(self.archive, target)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual((target / "README.md").read_bytes(), b"DIRTY WORKTREE\n")
        self.assertEqual((target / "topics/t1/STATUS.md").read_text(encoding="utf-8"), "unknown\n")
        self.assertFalse((target / ".git").exists())
        with contextlib.closing(sqlite3.connect(str(target / "state/topics.sqlite"))) as conn:
            self.assertEqual(conn.execute("SELECT id, state FROM topics").fetchall(), [("t1", "unknown")])
        self.assertEqual(restored["runtime_state"], "unknown")

    def test_git_bundle_roundtrip_keeps_history_and_dirty_bytes(self):
        def git_text(repo, *args):
            result = subprocess.run(["git", "-C", str(repo), *args],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout.strip()

        head = git_text(self.root, "rev-parse", "HEAD")
        branch = git_text(self.root, "symbolic-ref", "--short", "HEAD")
        subprocess.run(["git", "-C", str(self.root), "tag", "recovery-roundtrip"],
                       check=True)
        before_status = git_text(self.root, "status", "--porcelain", "--untracked-files=all")
        archive = Path(self.tmp.name) / "git.zip"
        result = self.export(archive)
        self.assertEqual(result["git_repositories"], 1)
        self.assertEqual(result["git_bundles"], 1)
        manifest = recovery.verify(archive)["manifest"]
        repo = manifest["git"]["repositories"][0]
        self.assertEqual(repo["path"], ".")
        self.assertEqual(repo["head"], head)
        self.assertEqual(repo["branch"], branch)
        self.assertTrue(repo["dirty"])
        self.assertEqual(git_text(self.root, "rev-parse", "HEAD"), head)
        self.assertEqual(git_text(self.root, "symbolic-ref", "--short", "HEAD"), branch)
        self.assertEqual(git_text(self.root, "status", "--porcelain", "--untracked-files=all"),
                         before_status)
        self.assertEqual((self.root / "README.md").read_bytes(), b"DIRTY WORKTREE\n")

        target = Path(self.tmp.name) / "git-restored"
        restored = recovery.restore(archive, target, rebuild_git=True)
        self.assertEqual(restored["git_bundles"], 1)
        self.assertEqual(len(restored["git_rebuilt"]), 1)
        self.assertTrue((target / ".git").is_dir())
        self.assertEqual(git_text(target, "rev-parse", "HEAD"), head)
        self.assertEqual(git_text(target, "symbolic-ref", "--short", "HEAD"), branch)
        self.assertIn("README.md", git_text(target, "diff", "--name-only"))
        self.assertEqual(git_text(target, "tag", "--list"), "recovery-roundtrip")
        self.assertEqual((target / "README.md").read_bytes(), b"DIRTY WORKTREE\n")
        config = (target / ".git" / "config").read_text(encoding="utf-8")
        self.assertNotIn(str(self.root), config)
        self.assertNotIn("worktrees", config)

    def test_exclude_wins_over_include_and_is_recorded(self):
        out = self.root / "AutoDL实验" / "tool" / "out"
        out.mkdir(parents=True)
        (out / "big.bin").write_bytes(b"x" * 32)
        (out.parent / "keep.py").write_bytes(b"keep\n")
        archive = self.root / ".local" / "exclude.zip"
        self.export(archive, include=["AutoDL实验/tool/out/big.bin"],
                    exclude=["AutoDL实验/tool/out"])
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        self.assertNotIn("AutoDL实验/tool/out/big.bin", names)
        self.assertIn("AutoDL实验/tool/keep.py", names)
        manifest = recovery.verify(archive)["manifest"]
        self.assertIn("AutoDL实验/tool/out", manifest["selection"]["exclude"])
        self.assertTrue(any(item["path"] == "AutoDL实验/tool/out" and item["reason"] == "excluded"
                            for item in manifest["skipped"]))
        cli_archive = self.root / ".local" / "exclude-cli.zip"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = recovery_cli.main(["export", "--workspace", str(self.root),
                                      "--archive", str(cli_archive),
                                      "--exclude", "AutoDL实验/tool/out", "--quiet"])
        self.assertEqual(code, 0, code)
        with zipfile.ZipFile(cli_archive) as zf:
            self.assertNotIn("AutoDL实验/tool/out/big.bin", set(zf.namelist()))

    def test_external_gitdir_is_rejected_without_following_it(self):
        outside = Path(self.tmp.name) / "outside-git"
        outside.mkdir()
        (outside / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        bad = self.root / "bad-repo"
        bad.mkdir()
        (bad / ".git").write_text(f"gitdir: {outside}\n", encoding="utf-8")
        archive = Path(self.tmp.name) / "external-git.zip"
        with self.assertRaises(recovery.RecoveryError) as caught:
            recovery.export(self.root, archive)
        self.assertIn("outside workspace", str(caught.exception))
        self.assertFalse(archive.exists())

    def test_cross_device_fallback_uses_second_staging(self):
        archive = Path(self.tmp.name) / "cross.zip"
        self.export(archive)
        target = Path(self.tmp.name) / "cross-restored"
        real_rename = os.rename
        calls = []

        def fake_rename(source, destination):
            calls.append((source, destination))
            if len(calls) == 1:
                raise OSError(errno.EXDEV, "synthetic cross-device")
            return real_rename(source, destination)

        with mock.patch.object(recovery.os, "rename", side_effect=fake_rename):
            restored = recovery.restore(archive, target, rebuild_git=True)
        self.assertTrue(restored["ok"], restored)
        self.assertEqual(len(calls), 2)
        self.assertEqual(Path(calls[0][1]), Path(str(recovery._long_path(target))))
        self.assertIn(".deliver-", Path(calls[1][0]).name)
        self.assertEqual((target / "README.md").read_bytes(), b"DIRTY WORKTREE\n")
        self.assertTrue((target / ".git").is_dir())
        result = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(target.parent.glob(f".{target.name}.deliver-*")), [])
        self.assertFalse(any(path.name.startswith(f".{target.name}.deliver-")
                             for path in target.parent.iterdir()))

    def test_cross_device_copy_failure_cleans_both_stages(self):
        archive = Path(self.tmp.name) / "cross-fail.zip"
        self.export(archive)
        target = Path(self.tmp.name) / "cross-fail"
        real_mkdtemp = tempfile.mkdtemp
        stages = []

        def track_mkdtemp(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            stages.append(Path(path))
            return path

        with mock.patch.object(recovery.tempfile, "mkdtemp", side_effect=track_mkdtemp):
            with mock.patch.object(recovery.os, "rename",
                                   side_effect=OSError(errno.EXDEV, "synthetic cross-device")):
                with mock.patch.object(recovery, "_copy_tree_long",
                                       side_effect=recovery.RecoveryError("synthetic copy failure")):
                    with self.assertRaises(recovery.RecoveryError):
                        recovery.restore(archive, target)
        self.assertFalse(target.exists())
        self.assertGreaterEqual(len(stages), 2)
        for stage in stages:
            self.assertFalse(recovery._long_path(stage).exists(), stage)
        self.assertEqual(list(target.parent.glob(f".{target.name}.deliver-*")), [])

    def test_long_windows_restore_path_with_rebuild_git(self):
        if os.name != "nt":
            self.skipTest("extended-length path behavior is Windows-specific")
        base = Path(self.tmp.name) / "long-root"
        self.addCleanup(lambda: recovery._rmtree_long(base, ignore_errors=True))
        segment = "d" * 90
        target = base / segment / segment / segment / "restored"
        self.assertGreater(len(str(target)), 260)
        self.assertFalse(target.parent.exists())
        archive = Path(self.tmp.name) / "long.zip"
        self.export(archive)
        restored = recovery.restore(archive, target, rebuild_git=True)
        self.assertTrue(restored["ok"], restored)
        long_target = recovery._long_path(target)
        self.assertTrue(long_target.is_dir())
        self.assertEqual((long_target / "README.md").read_bytes(), b"DIRTY WORKTREE\n")
        self.assertEqual((long_target / "topics/t1/STATUS.md").read_text(encoding="utf-8"),
                         "unknown\n")
        self.assertTrue((long_target / ".git").is_dir())
        self.assertEqual(restored["target"], str(target))

    def test_unborn_git_repository_only_needs_init(self):
        empty = self.root / "empty-repo"
        empty.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
        (empty / "untracked.txt").write_bytes(b"dirty\n")
        archive = Path(self.tmp.name) / "unborn.zip"
        result = self.export(archive)
        self.assertEqual(result["git_repositories"], 2)
        manifest = recovery.verify(archive)["manifest"]
        unborn = next(item for item in manifest["git"]["repositories"]
                      if item["path"] == "empty-repo")
        self.assertTrue(unborn["unborn"])
        self.assertIsNone(unborn["head"])
        self.assertIsNone(unborn["bundle"])
        target = Path(self.tmp.name) / "unborn-restored"
        recovery.restore(archive, target, rebuild_git=True)
        self.assertTrue((target / "empty-repo" / ".git").is_dir())
        self.assertEqual((target / "empty-repo" / "untracked.txt").read_bytes(), b"dirty\n")
        probe = subprocess.run(["git", "-C", str(target / "empty-repo"), "rev-parse", "--verify", "HEAD"],
                               capture_output=True, text=True)
        self.assertNotEqual(probe.returncode, 0)

    def test_default_privacy_excludes_and_explicit_include(self):
        self.export()
        with zipfile.ZipFile(self.archive) as zf:
            names = set(zf.namelist())
        for forbidden in (".kaggle_auth/token", ".ssh/id_rsa", ".env", "private/server.pem",
                          "data/keep.txt", "data/secret.csv", "submission/x", "archive/old",
                          "temp/x", ".venv/lib/python", "node_modules/pkg/index.js"):
            self.assertNotIn(forbidden, names)
        self.assertIn(".env.example", names)

        second = self.root / ".local" / "included.zip"
        self.export(second, include=["data/keep.txt", ".kaggle_auth/token", "private/server.pem"])
        with zipfile.ZipFile(second) as zf:
            names = set(zf.namelist())
        self.assertIn("data/keep.txt", names)
        self.assertNotIn("data/secret.csv", names)
        self.assertNotIn(".kaggle_auth/token", names)
        self.assertNotIn("private/server.pem", names)

    def test_live_runtime_sqlite3_wal_sidecars_are_hard_excluded(self):
        db = self.root / "state" / "runtime.sqlite3"
        writer = sqlite3.connect(str(db))
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE runtime_state(id TEXT PRIMARY KEY, value TEXT)")
        writer.execute("INSERT INTO runtime_state VALUES ('live', 'unknown')")
        writer.commit()
        wal = Path(str(db) + "-wal")
        shm = Path(str(db) + "-shm")
        self.assertTrue(wal.is_file())
        self.assertTrue(shm.is_file())

        archive = self.root / ".local" / "runtime.zip"
        result = self.export(archive, include=[
            "state/runtime.sqlite3-wal",
            "state/runtime.sqlite3-shm",
            "state/runtime.sqlite3-journal",
        ])
        with zipfile.ZipFile(archive) as zf:
            names = set(zf.namelist())
        self.assertIn("state/runtime.sqlite3", names)
        for name in ("state/runtime.sqlite3-wal", "state/runtime.sqlite3-shm",
                     "state/runtime.sqlite3-journal"):
            self.assertNotIn(name, names)
        manifest = recovery.verify(archive)["manifest"]
        skipped = {(item["path"], item["reason"]) for item in manifest["skipped"]}
        self.assertIn(("state/runtime.sqlite3-wal", "sqlite-sidecar"), skipped)
        self.assertIn(("state/runtime.sqlite3-shm", "sqlite-sidecar"), skipped)
        self.assertTrue(any(entry["path"] == "state/runtime.sqlite3" for entry in manifest["entries"]))

        target = Path(self.tmp.name) / "runtime-restored"
        recovery.restore(archive, target)
        with contextlib.closing(sqlite3.connect(str(target / "state" / "runtime.sqlite3"))) as conn:
            self.assertEqual(conn.execute("SELECT id, value FROM runtime_state").fetchall(),
                             [("live", "unknown")])
        self.assertFalse((target / "state" / "runtime.sqlite3-wal").exists())
        self.assertFalse((target / "state" / "runtime.sqlite3-shm").exists())
        self.assertEqual(result["sqlite"], 2)

    def test_symlink_is_never_read_or_stored(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_text("OUTSIDE\n", encoding="utf-8")
        link = self.root / "linked.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        self.export()
        with zipfile.ZipFile(self.archive) as zf:
            names = set(zf.namelist())
        self.assertNotIn("linked.txt", names)
        manifest = recovery.verify(self.archive)["manifest"]
        self.assertTrue(any(item["path"] == "linked.txt" and item["reason"] == "symlink"
                            for item in manifest["skipped"]))

    def test_bad_hash_fails_before_writing_target(self):
        self.export()
        bad = Path(self.tmp.name) / "bad.zip"
        rewrite_member(self.archive, bad, "README.md", lambda data: data[:-1] + b"X")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify(bad)
        target = Path(self.tmp.name) / "bad-target"
        with self.assertRaises(recovery.RecoveryError):
            recovery.restore(bad, target)
        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.glob(f".{target.name}.restore-*")), [])
        nested = Path(self.tmp.name) / "never-created" / "out"
        with self.assertRaises(recovery.RecoveryError):
            recovery.restore(bad, nested)
        self.assertFalse(nested.parent.exists())

    def test_path_escape_and_duplicate_members_are_rejected(self):
        self.export()
        manifest, payloads = load_archive(self.archive)
        bad_manifest = json.loads(json.dumps(manifest))
        bad_manifest["entries"][0]["path"] = "../evil.txt"
        escape = Path(self.tmp.name) / "escape.zip"
        write_archive(escape, bad_manifest, {"../evil.txt": b"evil"})
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify(escape)

        duplicate = Path(self.tmp.name) / "duplicate.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as zf:
                zf.writestr(recovery.MANIFEST_NAME, json.dumps(manifest))
                zf.writestr("README.md", b"one")
                zf.writestr("README.md", b"two")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify(duplicate)

    def test_symlink_member_is_rejected(self):
        self.export()
        manifest, payloads = load_archive(self.archive)
        link_manifest = json.loads(json.dumps(manifest))
        link_manifest["entries"].append({
            "path": "bad-link", "kind": "file", "bytes": 0, "sha256": sha(b""),
            "mode": 0o777, "mtime_ns": 0,
        })
        bad = Path(self.tmp.name) / "symlink.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr(recovery.MANIFEST_NAME, json.dumps(link_manifest))
            for name, data in payloads.items():
                zf.writestr(name, data)
            info = zipfile.ZipInfo("bad-link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "target")
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify(bad)

    def test_sqlite_sidecar_member_cannot_be_restored_independently(self):
        self.export()
        manifest, payloads = load_archive(self.archive)
        sidecar_manifest = json.loads(json.dumps(manifest))
        data = b"stale-wal"
        sidecar_manifest["entries"].append({
            "path": "state/topics.sqlite-wal", "kind": "file", "bytes": len(data),
            "sha256": sha(data), "mode": 0o600, "mtime_ns": 0,
        })
        bad = Path(self.tmp.name) / "sidecar.zip"
        sidecar_payloads = dict(payloads)
        sidecar_payloads["state/topics.sqlite-wal"] = data
        write_archive(bad, sidecar_manifest, sidecar_payloads)
        with self.assertRaises(recovery.RecoveryError):
            recovery.verify(bad)

    def test_existing_target_is_not_overwritten(self):
        self.export()
        target = Path(self.tmp.name) / "existing"
        target.mkdir()
        marker = target / "marker.txt"
        marker.write_text("keep\n", encoding="utf-8")
        with self.assertRaises(recovery.RecoveryError):
            recovery.restore(self.archive, target)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep\n")
        self.assertEqual(sorted(path.name for path in target.iterdir()), ["marker.txt"])

    def test_cli_and_python_m(self):
        archive = Path(self.tmp.name) / "cli.zip"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = recovery_cli.main(["export", "--workspace", str(self.root),
                                      "--archive", str(archive), "--quiet"])
        self.assertEqual(code, 0, code)
        self.assertTrue(archive.is_file())
        target = Path(self.tmp.name) / "cli-out"
        with contextlib.redirect_stdout(io.StringIO()):
            code = recovery_cli.main(["verify", "--archive", str(archive)])
            self.assertEqual(code, 0, code)
            code = recovery_cli.main(["restore", "--archive", str(archive), "--target", str(target)])
            self.assertEqual(code, 0, code)
        self.assertTrue((target / "README.md").is_file())
        result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-m",
                                 "kernel.autoresearch.recovery_cli", "--help"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
