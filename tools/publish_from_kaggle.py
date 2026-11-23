"""把本地 Kaggle 工作区里的**配置文档**发布到本仓库，并在发布前做一次凭证扫描。

## 为什么要有这个工具

「把本地某个工作区的配置提交上来」这句话里藏着一个很容易犯的错：
**把凭证一起提交上去。** 手工 copy 时你不会逐个文件看有没有 `KGAT_` 开头的密钥。

所以发布动作必须是**可重复、会拒绝**的：

1. 只复制白名单里列出的文件（不是整个目录）；
2. 每个目标文件加一段来源头（写清来源路径、sha256、原修改时间）；
3. **扫到疑似凭证就整体放弃**，一个文件都不写；
4. 产出一份 `MANIFEST.json`，下次发布可以比对「源文件变没变」。

## 用法

    python tools/publish_from_kaggle.py --src "C:\\Program Files\\agent\\kaggle_competition" \
        --dest docs/from-kaggle

默认白名单见 `DEFAULT_FILES`。想加文件就显式加 —— **不加整个目录**。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

#: 只发布这些（相对 Kaggle 工作区根）。每一份都人工确认过不含凭证。
DEFAULT_FILES = (
    "profiles/README.md",
    "INSTALL.md",
    "Kaggle平台/AGENTS.md",
    "Kaggle平台/实验/loop_2026-11-23_local_swe_probe/agent_attempts/bench-3tasks-summary.md",
    "Kaggle平台/实验/loop_2026-11-23_local_swe_probe/agent_attempts/bench-3tasks-summary.json",
)

#: 疑似凭证的特征。**命中任何一个就整体放弃**（宁可少发，不可错发）。
SECRET_PATTERNS = (
    r"KGAT_[A-Za-z0-9]{10,}",              # Kaggle API token
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",  # 私钥
    r"\bghp_[A-Za-z0-9]{20,}",             # GitHub token
    r"\bsk-[A-Za-z0-9]{20,}",              # 常见 OpenAI 风格 key
    r"(?i)(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"\s]{16,}['\"]",
    r"(?i)authorization\s*:\s*bearer\s+[A-Za-z0-9._\-]{20,}",
)


def find_secrets(text: str) -> list:
    """返回命中的模式（不返回命中的内容 —— 免得把密钥打到日志里）。"""
    hits = []
    for pat in SECRET_PATTERNS:
        if re.search(pat, text):
            hits.append(pat)
    return hits


def publish(src: pathlib.Path, dest: pathlib.Path, files=DEFAULT_FILES,
            dry_run: bool = False) -> dict:
    src = pathlib.Path(src)
    dest = pathlib.Path(dest)

    # 第一遍：全部读进来并扫描。**扫完再写** —— 不能写了一半才发现有毒。
    payload = []
    for rel in files:
        p = src / rel
        if not p.is_file():
            raise SystemExit(f"白名单里的文件不存在：{p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        hits = find_secrets(text)
        if hits:
            raise SystemExit(
                f"拒绝发布：{rel} 命中疑似凭证模式 {hits}\n"
                f"（工具不会把命中的内容打出来。请先清理源文件，或把它从白名单里去掉）")
        payload.append({
            "rel": rel,
            "text": text,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "bytes": p.stat().st_size,
            "mtime": p.stat().st_mtime,
        })

    manifest = {"source_root": str(src), "files": []}
    if dry_run:
        for item in payload:
            manifest["files"].append({"from": item["rel"], "sha256": item["sha256"],
                                      "bytes": item["bytes"]})
        return manifest

    dest.mkdir(parents=True, exist_ok=True)
    for item in payload:
        # 扁平化到 dest 下，但保留可读的名字
        name = item["rel"].replace("\\", "/").replace("/", "__")
        header = (
            f"<!-- 来源：Kaggle 工作区 `{item['rel']}`\n"
            f"     sha256={item['sha256'][:16]}…  {item['bytes']} 字节\n"
            f"     由 tools/publish_from_kaggle.py 发布；凭证扫描已通过 -->\n\n"
        )
        (dest / name).write_text(header + item["text"], encoding="utf-8", newline="\n")
        manifest["files"].append({"from": item["rel"], "to": name,
                                  "sha256": item["sha256"], "bytes": item["bytes"]})

    (dest / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dest", default="docs/from-kaggle")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    m = publish(pathlib.Path(args.src), pathlib.Path(args.dest), dry_run=args.dry_run)
    verb = "将发布" if args.dry_run else "已发布"
    print(f"{verb} {len(m['files'])} 个文件 → {args.dest}")
    for f in m["files"]:
        print(f"  {f['bytes']:>8} B  sha256={f['sha256'][:12]}…  {f['from']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
