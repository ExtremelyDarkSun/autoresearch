# GENERATED-BY: independent recovery tool, 2026-11-09
# LAST-EDIT: 2026-11-09
# PURPOSE: CLI for private recovery snapshot export/verify/restore.
# ORIGIN: kernel/autoresearch/recovery.py; no private content.
# CUMULATIVE-DELTA: First version; export defaults to .local, verify is read-only, restore requires a new target.

"""Command line entry point: ``python -m kernel.autoresearch.recovery_cli``."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile

from .recovery import RecoveryError, export, restore, verify


def _dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m kernel.autoresearch.recovery_cli",
        description="Private recovery snapshots: export current worktree, verify, restore to a new directory.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    out = sub.add_parser("export", help="Export the current filesystem worktree (not Git HEAD)")
    out.add_argument("--workspace", required=True, help="Existing private workspace root")
    out.add_argument("--archive", help="Output .zip; default <workspace>/.local/recovery/")
    out.add_argument("--include", action="append", default=[],
                     help="Additive path/glob to re-enable a soft-excluded path; credentials still denied")
    out.add_argument("--exclude", action="append", default=[],
                     help="Subtractive path/glob; wins over --include and is recorded in the manifest")
    out.add_argument("--json", action="store_true", help="Print JSON only on stdout")
    out.add_argument("--quiet", action="store_true", help="Suppress the success summary")

    check = sub.add_parser("verify", help="Read-only verify every archive member and hash")
    check.add_argument("--archive", required=True)
    check.add_argument("--json", action="store_true")

    put = sub.add_parser("restore", help="Verify fully, then create a brand-new target directory")
    put.add_argument("--archive", required=True)
    put.add_argument("--target", required=True, help="New directory; must not exist")
    put.add_argument("--rebuild-git", action="store_true",
                     help="Explicitly init/fetch bundled history and reset --mixed; never checkout")
    put.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    try:
        options = parser.parse_args(args)
        if options.command == "export":
            result = export(options.workspace, options.archive,
                            include=options.include, exclude=options.exclude)
            warning = result.get("warning")
            if warning:
                print("警告：" + warning, file=sys.stderr)
            if options.json or not options.quiet:
                _dump(result)
            return 0
        if options.command == "verify":
            _dump(verify(options.archive))
            return 0
        if options.command == "restore":
            _dump(restore(options.archive, options.target, rebuild_git=options.rebuild_git))
            return 0
    except RecoveryError as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    except zipfile.LargeZipFile as exc:
        _dump({"ok": False, "error": type(exc).__name__, "reason": str(exc)})
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
